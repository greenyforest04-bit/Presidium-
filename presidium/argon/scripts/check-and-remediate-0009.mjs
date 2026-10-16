// Adapted from Argon 68. Never change locked totals, order statuses or disputes.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { SqliteD1 } from '../sqlite.mjs';

export function inferFees(row) {
  const valid = n => Number.isSafeInteger(n) && n >= 0;
  if (!valid(row.locked_price_kopecks) || !valid(row.buyer_total_kopecks) ||
      [row.delivery_kopecks, row.payment_operator_kopecks].some(n => n !== null && !valid(n)))
    return { conflict: 'invalid_financial_values' };
  const remaining = row.buyer_total_kopecks - row.locked_price_kopecks -
    (row.delivery_kopecks ?? 0) - (row.payment_operator_kopecks ?? 0);
  if (remaining < 0) return { conflict: 'known_components_exceed_locked_total' };
  const missing = Number(row.delivery_kopecks === null) + Number(row.payment_operator_kopecks === null);
  if (missing === 2 && remaining !== 0) return { conflict: 'ambiguous_fee_allocation' };
  if (missing === 0 && remaining !== 0) return { conflict: 'components_disagree_with_locked_total' };
  return {
    delivery: row.delivery_kopecks ?? (row.payment_operator_kopecks === null ? 0 : remaining),
    operator: row.payment_operator_kopecks ?? (row.delivery_kopecks === null ? 0 : remaining),
  };
}

export async function diagnoseMigration0009Conflicts(db) {
  const duplicateOrders = (await db.prepare(`SELECT listing_id,COUNT(*) AS active_count FROM market_orders
    WHERE status IN ('confirmed','paid_sandbox','paid','shipped','disputed') GROUP BY listing_id HAVING COUNT(*)>1`).all()).results;
  const duplicateOpenDisputes = (await db.prepare(`SELECT order_id,COUNT(*) AS open_count FROM market_disputes
    WHERE status='open' GROUP BY order_id HAVING COUNT(*)>1`).all()).results;
  const rows = (await db.prepare(`SELECT id,locked_price_kopecks,buyer_total_kopecks,delivery_kopecks,payment_operator_kopecks FROM market_orders`).all()).results;
  const nullFeeOrders = rows.filter(r => r.delivery_kopecks === null || r.payment_operator_kopecks === null);
  const financialConflicts = rows.flatMap(r => inferFees(r).conflict ? [{ orderId: r.id, reason: inferFees(r).conflict }] : []);
  return { hasConflicts: !!(duplicateOrders.length || duplicateOpenDisputes.length || nullFeeOrders.length || financialConflicts.length),
    duplicateOrders, duplicateOpenDisputes, nullFeeOrders, financialConflicts,
    obligationConflictsRequireManualResolution: !!(duplicateOrders.length || duplicateOpenDisputes.length) };
}

export async function remediateMigration0009Conflicts(db, now = Date.now()) {
  const before = await diagnoseMigration0009Conflicts(db), actions = [];
  for (const row of before.nullFeeOrders) {
    const inferred = inferFees(row);
    if (inferred.conflict) continue;
    const result = await db.prepare(`UPDATE market_orders SET delivery_kopecks=?,payment_operator_kopecks=?,updated_at=?
      WHERE id=? AND buyer_total_kopecks=? AND locked_price_kopecks=? AND delivery_kopecks IS ? AND payment_operator_kopecks IS ?`)
      .bind(inferred.delivery, inferred.operator, now, row.id, row.buyer_total_kopecks, row.locked_price_kopecks, row.delivery_kopecks, row.payment_operator_kopecks).run();
    if (result.meta.changes) actions.push({ type: 'infer_missing_component', orderId: row.id, ...inferred });
  }
  return { before, actions, after: await diagnoseMigration0009Conflicts(db) };
}

if (process.argv[1] && path.resolve(process.argv[1]) === fileURLToPath(import.meta.url)) {
  const file = process.argv[2], apply = process.argv.includes('--apply');
  if (!file || !fs.existsSync(file)) throw Error('Expected an existing SQLite database. Default is read-only; --apply changes only uniquely inferable fee components.');
  const db = new SqliteD1(file, { readOnly: !apply });
  try {
    const report = apply ? await remediateMigration0009Conflicts(db) : await diagnoseMigration0009Conflicts(db);
    console.log(JSON.stringify(report, null, 2));
    process.exitCode = (report.after ?? report).hasConflicts ? 2 : 0;
  } finally { db.close(); }
}

import { test } from 'node:test';
import assert from 'node:assert/strict';
import { SqliteD1 } from '../sqlite.mjs';
import { inferFees, remediateMigration0009Conflicts } from '../scripts/check-and-remediate-0009.mjs';

test('paid totals survive partial fee recovery; ambiguity remains a conflict', async () => {
  const db = new SqliteD1(':memory:');
  db.exec(`CREATE TABLE market_orders(id TEXT, listing_id TEXT,status TEXT,locked_price_kopecks INTEGER,buyer_total_kopecks INTEGER,delivery_kopecks INTEGER,payment_operator_kopecks INTEGER,updated_at INTEGER);
    CREATE TABLE market_disputes(id TEXT,order_id TEXT,status TEXT);`);
  const fixtures = [['a','listing','paid',1000,1300,null,100], ['b','listing','paid',1000,1300,200,null],
    ['c','other','paid',1000,1300,null,null], ['d','third','paid',1000,900,null,100]];
  try {
    for (const row of fixtures) await db.prepare('INSERT INTO market_orders VALUES(?,?,?,?,?,?,?,0)').bind(...row).run();
    db.exec("INSERT INTO market_disputes VALUES('d1','a','open'),('d2','a','open')");
    const result = await remediateMigration0009Conflicts(db);
    const rows = (await db.prepare('SELECT * FROM market_orders ORDER BY id').all()).results;
    assert.deepEqual(rows.map(r=>r.buyer_total_kopecks), [1300,1300,1300,900]);
    assert.deepEqual([rows[0].delivery_kopecks,rows[0].payment_operator_kopecks],[200,100]);
    assert.deepEqual([rows[1].delivery_kopecks,rows[1].payment_operator_kopecks],[200,100]);
    assert.equal(rows[2].delivery_kopecks,null);
    assert.equal(rows[3].delivery_kopecks,null);
    assert(rows.every(r=>r.status==='paid'));
    assert((await db.prepare('SELECT * FROM market_disputes').all()).results.every(r=>r.status==='open'));
    assert.equal(result.after.hasConflicts,true);
    assert.equal(result.after.obligationConflictsRequireManualResolution,true);
    assert.equal(inferFees({locked_price_kopecks:1000,buyer_total_kopecks:1000,delivery_kopecks:null,payment_operator_kopecks:null}).delivery,0);
  } finally { db.close(); }
});

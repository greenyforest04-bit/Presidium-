import { DatabaseSync } from 'node:sqlite';

/** Real SQLite adapter for the subset of D1 used by the recovered services. */
export class SqliteD1 {
  constructor(path, options = {}) {
    this.connection = new DatabaseSync(path, options);
    this.connection.exec('PRAGMA foreign_keys=ON; PRAGMA busy_timeout=15000;');
  }
  prepare(sql) {
    const db = this;
    return new Statement(db, sql);
  }
  async batch(statements) {
    const nested = this.connection.isTransaction;
    this.connection.exec(nested ? 'SAVEPOINT d1_batch' : 'BEGIN IMMEDIATE');
    try {
      const results = statements.map(s => s.execute());
      this.connection.exec(nested ? 'RELEASE d1_batch' : 'COMMIT');
      return results;
    } catch (error) { this.connection.exec(nested ? 'ROLLBACK TO d1_batch; RELEASE d1_batch' : 'ROLLBACK'); throw error; }
  }
  exec(sql) { this.connection.exec(sql); }
  close() { this.connection.close(); }
}
class Statement {
  constructor(db, sql, args = []) { this.db = db; this.sql = sql; this.args = args; }
  bind(...args) { return new Statement(this.db, this.sql, args); }
  execute() {
    const stmt = this.db.connection.prepare(this.sql);
    const info = stmt.run(...this.args);
    return { success: true, results: [], meta: { changes: Number(info.changes), last_row_id: Number(info.lastInsertRowid) } };
  }
  async run() { return this.execute(); }
  async first(column) {
    const row = this.db.connection.prepare(this.sql).get(...this.args) ?? null;
    return column ? row?.[column] ?? null : row;
  }
  async all() { return { success: true, results: this.db.connection.prepare(this.sql).all(...this.args), meta: { changes: 0 } }; }
}

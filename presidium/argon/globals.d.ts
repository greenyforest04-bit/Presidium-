interface D1Result<T = Record<string, unknown>> { success: boolean; results: T[]; meta: { changes: number; last_row_id?: number } }
interface D1PreparedStatement {
  bind(...args: unknown[]): D1PreparedStatement;
  first<T = Record<string, unknown>>(column?: string): Promise<T | null>;
  all<T = Record<string, unknown>>(): Promise<D1Result<T>>;
  run<T = Record<string, unknown>>(): Promise<D1Result<T>>;
}
interface D1Database { prepare(sql: string): D1PreparedStatement; batch<T = Record<string, unknown>>(statements: D1PreparedStatement[]): Promise<D1Result<T>[]> }

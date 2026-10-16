-- A local sandbox receipt is evidence of simulation, never of a real payment.
-- Missing provenance must block financial dispute settlement, not infer a refund.
CREATE TABLE market_payment_provenance (
  order_id TEXT PRIMARY KEY REFERENCES market_orders(id),
  kind TEXT NOT NULL CHECK(kind='sandbox'),
  created_at INTEGER NOT NULL
);

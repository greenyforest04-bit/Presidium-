"""Explicit local test entitlement. Not an HTTP billing or payment endpoint."""
import argparse
from pathlib import Path
import sqlite3
import time

parser=argparse.ArgumentParser(description=__doc__)
parser.add_argument("user_id",help="Existing Matrix profile ID; open Profile in the application first")
parser.add_argument("--database",type=Path,default=Path(".data/product/product.sqlite3"))
parser.add_argument("--tier",choices=["plus","business"],default="plus")
parser.add_argument("--days",type=int,default=7)
args=parser.parse_args()
if not args.database.exists(): parser.error("Database missing; open Profile in the application first")
if not 1<=args.days<=30: parser.error("Local test grants must be 1..30 days")
now=int(time.time()*1000)
with sqlite3.connect(args.database) as db:
    if not db.execute("SELECT 1 FROM users WHERE id=?",(args.user_id,)).fetchone(): parser.error("User profile missing; sign in and open Profile first")
    db.execute("INSERT INTO subscriptions(user_id,tier,status,period_start,period_end,updated_at,last_event_id) VALUES(?,?,'active',?,?,?,'local-test-grant') ON CONFLICT(user_id) DO UPDATE SET tier=excluded.tier,status=excluded.status,period_start=excluded.period_start,period_end=excluded.period_end,updated_at=excluded.updated_at,last_event_id=excluded.last_event_id",(args.user_id,args.tier,now,now+args.days*86400000,now))
print(f"LOCAL TEST ONLY: {args.user_id}: {args.tier}, {args.days} days. No payment was processed.")

"""Versioned local SQLite installation of the verified Argon D1 schema.

This is product data, not the desktop encrypted history or Matrix crypto store.
Existing migration files are immutable and checked against their recorded hash.
"""
import hashlib
from pathlib import Path
import sqlite3


class MigrationConflict(RuntimeError):
    pass


def migrate(path: Path, through: str | None = None) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path, timeout=30) as db:
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("CREATE TABLE IF NOT EXISTS product_migrations(name TEXT PRIMARY KEY,sha256 TEXT NOT NULL)")
        db.commit()
        for file in sorted(Path(__file__).with_name("migrations").glob("*.sql")):
            if through and file.name > through:
                break
            sql = file.read_text(encoding="utf-8")
            digest = hashlib.sha256(file.read_bytes()).hexdigest()
            db.execute("BEGIN IMMEDIATE")
            applied = db.execute("SELECT sha256 FROM product_migrations WHERE name=?", (file.name,)).fetchone()
            if applied:
                if applied[0] != digest:
                    raise MigrationConflict(f"Applied migration changed: {file.name}")
                db.commit()
                continue
            if file.name.startswith("0009"):
                # Diagnose obligations before running the original guard/DDL.
                duplicates = db.execute("SELECT listing_id FROM market_orders WHERE status IN ('confirmed','paid_sandbox','paid','shipped','disputed') GROUP BY listing_id HAVING count(*)>1").fetchall()
                disputes = db.execute("SELECT order_id FROM market_disputes WHERE status='open' GROUP BY order_id HAVING count(*)>1").fetchall()
                fees = db.execute("SELECT id FROM market_orders WHERE delivery_kopecks IS NULL OR payment_operator_kopecks IS NULL OR buyer_total_kopecks != locked_price_kopecks+delivery_kopecks+payment_operator_kopecks").fetchall()
                if duplicates or disputes or fees:
                    raise MigrationConflict(f"0009 blocked; resolve obligations explicitly. Duplicate listings={duplicates}, disputes={disputes}, financial conflicts={fees}. No statuses or locked totals were changed.")
            try:
                statement = ""
                for character in sql:
                    statement += character
                    if character == ";" and sqlite3.complete_statement(statement):
                        db.execute(statement)
                        statement = ""
                if statement.strip():
                    db.execute(statement)
                db.execute("INSERT INTO product_migrations VALUES (?,?)", (file.name, digest))
                db.commit()
            except Exception:
                db.rollback()
                raise

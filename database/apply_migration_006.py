from __future__ import annotations
import argparse, sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/database/market_data_v2.db"
MIGRATION = ROOT / "database/migrations/006_market_sessions.sql"

def column_exists(conn, table, col):
    return any(r[1] == col for r in conn.execute(f"PRAGMA table_info({table})").fetchall())

def apply(db: Path = DB, migration: Path = MIGRATION):
    conn = sqlite3.connect(db)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        sql = migration.read_text(encoding="utf-8")
        # SQLite ALTER TABLE ADD COLUMN is not idempotent, so apply statements one-by-one.
        for raw in sql.split(";"):
            stmt = raw.strip()
            if not stmt or stmt.upper() in {"BEGIN", "COMMIT"}:
                continue
            up = stmt.upper()
            if up.startswith("ALTER TABLE PRICE_BARS ADD COLUMN"):
                col = stmt.split()[-1]
                if column_exists(conn, "price_bars", col):
                    continue
            if up.startswith("ALTER TABLE REALIZED_OUTCOMES ADD COLUMN"):
                col = stmt.split()[-1]
                if column_exists(conn, "realized_outcomes", col):
                    continue
            conn.execute(stmt)
        conn.commit()
        checks = {
            "market_sessions": conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table' AND name='market_sessions'"
            ).fetchone() is not None,
            "price_bars.session_id": column_exists(conn, "price_bars", "session_id"),
            "price_bars.trading_day": column_exists(conn, "price_bars", "trading_day"),
            "realized_outcomes.horizon_scope": column_exists(conn, "realized_outcomes", "horizon_scope"),
        }
        print({"migration":"006_market_sessions","checks":checks,"db":str(db)})
    finally:
        conn.close()

if __name__ == "__main__":
    ap=argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DB)
    ap.add_argument("--migration", type=Path, default=MIGRATION)
    args=ap.parse_args()
    apply(args.db,args.migration)

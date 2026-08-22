from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
MIGRATION = ROOT / "database" / "migrations" / "002_market_foundation.sql"


def column_exists(conn: sqlite3.Connection, table: str, column: str) -> bool:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    return any(row[1] == column for row in rows)


def apply(db_path: Path) -> None:
    if not db_path.exists():
        raise FileNotFoundError(f"DB no existe: {db_path}")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys=ON")
        statements = [
            ("observed_bars", "ALTER TABLE realized_outcomes ADD COLUMN observed_bars INTEGER"),
            ("expected_bars", "ALTER TABLE realized_outcomes ADD COLUMN expected_bars INTEGER"),
            ("coverage_pct", "ALTER TABLE realized_outcomes ADD COLUMN coverage_pct REAL"),
            ("max_gap_seconds", "ALTER TABLE realized_outcomes ADD COLUMN max_gap_seconds REAL"),
            ("session_count", "ALTER TABLE realized_outcomes ADD COLUMN session_count INTEGER"),
            ("data_quality", "ALTER TABLE realized_outcomes ADD COLUMN data_quality TEXT"),
            ("quality_version", "ALTER TABLE realized_outcomes ADD COLUMN quality_version TEXT"),
        ]
        for column, sql in statements:
            if not column_exists(conn, "realized_outcomes", column):
                conn.execute(sql)

        conn.execute(
            "CREATE INDEX IF NOT EXISTS idx_outcomes_quality "
            "ON realized_outcomes(asset_id, horizon_seconds, coverage_pct)"
        )
        conn.execute(
            "CREATE TABLE IF NOT EXISTS schema_meta "
            "(key TEXT PRIMARY KEY, value TEXT NOT NULL)"
        )
        conn.execute(
            "INSERT OR IGNORE INTO schema_meta(key, value) "
            "VALUES ('market_foundation_version', '0.1.0')"
        )
        conn.commit()
    finally:
        conn.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    apply(args.db)
    print(f"Migración 002 aplicada/verificada: {args.db}")

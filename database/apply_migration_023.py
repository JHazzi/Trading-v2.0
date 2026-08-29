from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
MIGRATION = (
    ROOT / "database" / "migrations"
    / "023_daily_price_first_quality_eligible.sql"
)
VERSION = "023"
NAME = "daily_price_first_quality_eligible"


def apply(db: Path = DEFAULT_DB) -> str:
    if not MIGRATION.is_file():
        raise FileNotFoundError(MIGRATION)
    if not db.is_file():
        raise FileNotFoundError(db)
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        row = conn.execute(
            "SELECT name FROM schema_migrations WHERE version=?",
            (VERSION,),
        ).fetchone()
        if row is not None:
            if str(row[0]) != NAME:
                raise RuntimeError(
                    f"migration {VERSION} already used by {row[0]!r}"
                )
            return "already_applied"
        conn.executescript(MIGRATION.read_text(encoding="utf-8"))
        stored = conn.execute(
            "SELECT name FROM schema_migrations WHERE version=?",
            (VERSION,),
        ).fetchone()
        if stored is None or str(stored[0]) != NAME:
            raise RuntimeError("migration 023 did not register itself")
        conn.commit()
    return "applied"


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    print(apply(args.db))
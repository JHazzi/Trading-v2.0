from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
MIGRATION = (
    ROOT / "database" / "migrations"
    / "019_event_graph_brain_foundation.sql"
)
VERSION = "019"
NAME = "event_graph_brain_foundation"


def apply(db: Path = DEFAULT_DB) -> str:
    if not db.is_file():
        raise FileNotFoundError(db)
    if not MIGRATION.is_file():
        raise FileNotFoundError(MIGRATION)

    sql = MIGRATION.read_text(encoding="utf-8")
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        if "schema_migrations" not in tables:
            raise RuntimeError(
                "schema_migrations missing; apply repository base migrations first"
            )

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

        conn.executescript(sql)
        stored = conn.execute(
            "SELECT name FROM schema_migrations WHERE version=?",
            (VERSION,),
        ).fetchone()
        if stored is None or str(stored[0]) != NAME:
            raise RuntimeError("migration 019 did not register itself")
        conn.commit()
    return "applied"


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = p.parse_args()
    print(apply(a.db))

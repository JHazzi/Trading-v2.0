from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
MIGRATION = (
    ROOT / "database" / "migrations"
    / "020_event_graph_brain_foundation.sql"
)
VERSION = "020"
NAME = "event_graph_brain_foundation"


def applied_migrations(conn: sqlite3.Connection) -> list[tuple[str, str]]:
    return [
        (str(v), str(n))
        for v, n in conn.execute(
            """
            SELECT version,name
            FROM schema_migrations
            ORDER BY
              CASE
                WHEN version GLOB '[0-9]*'
                THEN CAST(version AS INTEGER)
                ELSE 2147483647
              END,
              version
            """
        ).fetchall()
    ]


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
                history = applied_migrations(conn)
                raise RuntimeError(
                    f"migration {VERSION} already used by {row[0]!r}. "
                    f"Do not overwrite it. Applied migrations tail={history[-8:]}"
                )
            return "already_applied"

        # Guard that the local Event Brain migration we discovered remains
        # untouched. This is informational, not a dependency.
        local_019 = conn.execute(
            "SELECT name FROM schema_migrations WHERE version='019'"
        ).fetchone()
        if local_019 is None:
            raise RuntimeError(
                "Expected existing local migration 019 before applying the "
                "V002 overlay, but none was found. Inspect migration history "
                "instead of silently changing numbering."
            )

        conn.executescript(sql)
        stored = conn.execute(
            "SELECT name FROM schema_migrations WHERE version=?",
            (VERSION,),
        ).fetchone()
        if stored is None or str(stored[0]) != NAME:
            raise RuntimeError("migration 020 did not register itself")
        conn.commit()
    return "applied"


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    a = p.parse_args()
    print(apply(a.db))

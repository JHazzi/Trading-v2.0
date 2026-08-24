from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "database" / "market_data_v2.db"
MIGRATION = ROOT / "database" / "migrations" / "010_event_layer.sql"

REQUIRED = [
    "event_clusters",
    "event_cluster_news",
    "event_evidence",
    "event_states",
    "event_reaction_outcomes",
    "event_source_knowledge",
]

def main() -> None:
    sql = MIGRATION.read_text(encoding="utf-8")
    with sqlite3.connect(DB) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(sql)
        missing = [
            t for t in REQUIRED
            if conn.execute(
                "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
                (t,),
            ).fetchone() is None
        ]
        if missing:
            raise SystemExit(f"Faltan tablas del Event Layer: {missing}")
        conn.commit()

    print({
        "migration": "010_event_layer",
        "db": str(DB),
        "tables": REQUIRED,
        "status": "applied"
    })

if __name__ == "__main__":
    main()

from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/database/market_data_v2.db"
SQL = ROOT / "database/migrations/008_market_state_v002_390m.sql"


def main() -> None:
    with sqlite3.connect(DB) as conn:
        cols = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(market_state_v002_snapshots)"
            )
        }

        if "return_percentile_390m" not in cols:
            conn.executescript(SQL.read_text(encoding="utf-8"))
            conn.commit()

        cols = {
            row[1]
            for row in conn.execute(
                "PRAGMA table_info(market_state_v002_snapshots)"
            )
        }

        if "return_percentile_390m" not in cols:
            raise RuntimeError(
                "No se pudo agregar return_percentile_390m"
            )

        print(
            "Migration 008 applied/verificada: "
            f"{DB}"
        )


if __name__ == "__main__":
    main()

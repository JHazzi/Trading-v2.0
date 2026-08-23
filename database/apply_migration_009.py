from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data/database/market_data_v2.db"
SQL = ROOT / "database/migrations/009_asset_universe_membership.sql"


def main() -> None:
    with sqlite3.connect(DB) as conn:
        conn.executescript(SQL.read_text(encoding="utf-8"))

        # Primera reconstrucción histórica:
        # tratamos como universo "price_observed" el período en el que
        # realmente existen velas para cada activo.
        conn.execute("""
            INSERT OR IGNORE INTO asset_universe_membership (
                asset_id,
                universe,
                valid_from,
                valid_to,
                source,
                confidence
            )
            SELECT
                asset_id,
                'price_observed',
                MIN(timestamp),
                MAX(timestamp),
                'price_bars',
                1.0
            FROM price_bars
            WHERE interval = '1m'
            GROUP BY asset_id
        """)

        conn.commit()

        count = conn.execute("""
            SELECT COUNT(*)
            FROM asset_universe_membership
            WHERE universe = 'price_observed'
        """).fetchone()[0]

        print({
            "migration": "009_asset_universe_membership",
            "memberships": count,
            "db": str(DB),
        })


if __name__ == "__main__":
    main()

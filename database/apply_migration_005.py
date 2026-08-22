from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / 'data' / 'database' / 'market_data_v2.db'
SQL_PATH = ROOT / 'database' / 'migrations' / '005_target_dedup.sql'


def main() -> None:
    with sqlite3.connect(DB_PATH) as conn:
        conn.executescript(SQL_PATH.read_text(encoding='utf-8'))
        dup = conn.execute('''
            SELECT COUNT(*) FROM (
                SELECT asset_id, origin_time, horizon_seconds
                FROM realized_outcomes
                GROUP BY asset_id, origin_time, horizon_seconds
                HAVING COUNT(*) > 1
            )
        ''').fetchone()[0]
        total = conn.execute('SELECT COUNT(*) FROM realized_outcomes').fetchone()[0]
        print({
            'migration': '005_target_dedup',
            'rows': total,
            'duplicate_groups_remaining': dup,
        })


if __name__ == '__main__':
    main()

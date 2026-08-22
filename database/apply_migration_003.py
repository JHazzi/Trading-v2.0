from __future__ import annotations

import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB_PATH = ROOT / 'data' / 'database' / 'market_data_v2.db'
MIGRATION = ROOT / 'database' / 'migrations' / '003_target_quality.sql'


def main() -> int:
    conn = sqlite3.connect(DB_PATH)
    try:
        conn.execute('PRAGMA foreign_keys=ON')
        conn.executescript(MIGRATION.read_text(encoding='utf-8'))
        conn.commit()
    finally:
        conn.close()
    print(f'Migración 003 aplicada/verificada: {DB_PATH}')
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

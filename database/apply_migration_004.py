from pathlib import Path
import sqlite3

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / 'data' / 'database' / 'market_data_v2.db'
MIGRATION = Path(__file__).resolve().parent / 'migrations' / '004_target_idempotency.sql'

with sqlite3.connect(DB) as conn:
    conn.executescript(MIGRATION.read_text(encoding='utf-8'))
    dupes = conn.execute('''
        SELECT COUNT(*) FROM (
            SELECT asset_id, origin_time, horizon_seconds, COUNT(*) c
            FROM realized_outcomes
            GROUP BY asset_id, origin_time, horizon_seconds
            HAVING c > 1
        )
    ''').fetchone()[0]
    print(f'Migración 004 aplicada/verificada: {DB}')
    print(f'grupos duplicados restantes: {dupes}')

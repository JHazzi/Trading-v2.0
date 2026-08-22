from pathlib import Path
import sqlite3
ROOT=Path(__file__).resolve().parents[1]
DB=ROOT/'data/database/market_data_v2.db'
SQL=ROOT/'database/migrations/007_market_state_v002.sql'
def main():
    with sqlite3.connect(DB) as conn:
        conn.executescript(SQL.read_text(encoding='utf-8'))
    print(f'Migration 007 applied: {DB}')
if __name__=='__main__': main()

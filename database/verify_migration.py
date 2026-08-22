#!/usr/bin/env python3
"""Compare legacy/v2 row counts and key samples after migration."""
from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT.parent / "quant_market_bot" / "data" / "market_data.db"
DEFAULT_DEST = ROOT / "data" / "database" / "market_data_v2.db"


def count(conn, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument('--source', type=Path, default=DEFAULT_SOURCE)
    p.add_argument('--destination', type=Path, default=DEFAULT_DEST)
    p.add_argument('--output', type=Path, default=ROOT/'data'/'processed'/'migration_verification.json')
    args = p.parse_args()

    src = sqlite3.connect(args.source)
    dst = sqlite3.connect(args.destination)
    try:
        report = {
            'source': str(args.source),
            'destination': str(args.destination),
            'counts': {
                'assets': {'legacy': count(src,'universo_tickers'), 'v2': count(dst,'assets')},
                'price_bars': {'legacy': count(src,'precios'), 'v2': count(dst,'price_bars')},
                'news_documents': {'legacy': count(src,'noticias'), 'v2': count(dst,'news_documents')},
                'events': {'legacy_with_id': int(src.execute('SELECT COUNT(*) FROM noticias WHERE id_evento IS NOT NULL').fetchone()[0]), 'v2': count(dst,'events')},
                'relations': {'legacy': count(src,'relaciones_organicas'), 'v2': count(dst,'entity_relations')},
                'macro_rows': {'legacy': count(src,'macro_diario'), 'v2': count(dst,'macro_observations')},
                'legacy_correlations': {'legacy': count(src,'correlaciones'), 'v2': count(dst,'legacy_correlations')},
                'legacy_state_vectors': {'legacy': count(src,'vectores_estado'), 'v2': count(dst,'legacy_state_vectors')},
                'legacy_paper_trading': {'legacy': count(src,'paper_trading'), 'v2': count(dst,'legacy_paper_trading')},
            },
        }
        # Exact sample check for 100 deterministic price rows.
        samples = src.execute('SELECT ticker,timestamp,open,high,low,close,volume FROM precios ORDER BY ticker,timestamp LIMIT 100').fetchall()
        assets = {row[0]: row[1] for row in dst.execute('SELECT ticker,asset_id FROM assets')}
        mismatches = []
        for ticker, ts, op, hi, lo, close, vol in samples:
            aid = assets.get(ticker)
            row = dst.execute('SELECT open,high,low,close,volume FROM price_bars WHERE asset_id=? AND timestamp=? AND interval="1m" AND source="legacy:yfinance"',(aid,ts)).fetchone() if aid else None
            if row is None or tuple(row) != (op,hi,lo,close,vol):
                mismatches.append({'ticker':ticker,'timestamp':ts})
        report['sample_price_mismatches'] = mismatches
    finally:
        src.close(); dst.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report,indent=2,ensure_ascii=False),encoding='utf-8')
    print(json.dumps(report,indent=2,ensure_ascii=False))
    return 0 if not report['sample_price_mismatches'] else 2

if __name__ == '__main__':
    raise SystemExit(main())

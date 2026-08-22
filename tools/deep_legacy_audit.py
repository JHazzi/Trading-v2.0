#!/usr/bin/env python3
"""Deeper audit of legacy market/news data before migration.

Read-only. Designed to answer:
- How much real intraday coverage do we have per asset?
- Where are gaps larger than 1 minute / 5 minutes?
- How much data sits outside expected US regular-session hours?
- How concentrated are news sources and tickers?
- How often do news timestamps precede/overlap the first available price after publication?
"""
from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime
from pathlib import Path


def parse_ts(value: str) -> datetime:
    return datetime.fromisoformat(value.replace('Z', '+00:00'))


def audit_prices(conn: sqlite3.Connection) -> dict:
    assets = conn.execute("SELECT ticker FROM universo_tickers WHERE activo = 1 ORDER BY ticker").fetchall()
    per_asset = []
    total_gaps_5m = 0
    for (ticker,) in assets:
        rows = conn.execute(
            "SELECT timestamp FROM precios WHERE ticker=? ORDER BY timestamp", (ticker,)
        ).fetchall()
        if not rows:
            continue
        times = [parse_ts(r[0]) for r in rows]
        gaps_1m = []
        gaps_5m = []
        off_session = 0
        for a, b in zip(times, times[1:]):
            gap = (b - a).total_seconds() / 60.0
            if gap > 1.5:
                gaps_1m.append(gap)
            if gap > 5.0:
                gaps_5m.append(gap)
        for t in times:
            # US regular session in UTC is roughly 13:30-20:00 during DST.
            # This is deliberately diagnostic only; it is not used as a hard rule.
            minutes = t.hour * 60 + t.minute
            if not (13 * 60 + 30 <= minutes < 20 * 60):
                off_session += 1
        total_gaps_5m += len(gaps_5m)
        per_asset.append({
            'ticker': ticker,
            'rows': len(rows),
            'first': times[0].isoformat(),
            'last': times[-1].isoformat(),
            'gaps_gt_1_5m': len(gaps_1m),
            'gaps_gt_5m': len(gaps_5m),
            'largest_gap_minutes': max(gaps_1m, default=0),
            'off_regular_session_rows_approx': off_session,
        })
    return {
        'assets_with_price_data': len(per_asset),
        'total_gaps_gt_5m': total_gaps_5m,
        'top_assets_by_gap_count': sorted(per_asset, key=lambda x: x['gaps_gt_5m'], reverse=True)[:25],
        'top_assets_by_rows': sorted(per_asset, key=lambda x: x['rows'], reverse=True)[:25],
    }


def audit_news(conn: sqlite3.Connection) -> dict:
    source_rows = conn.execute(
        "SELECT COALESCE(fuente,'(NULL)'), COUNT(*) FROM noticias GROUP BY fuente ORDER BY COUNT(*) DESC"
    ).fetchall()
    ticker_rows = conn.execute(
        "SELECT ticker, COUNT(*) FROM noticias GROUP BY ticker ORDER BY COUNT(*) DESC LIMIT 25"
    ).fetchall()
    date_range = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM noticias").fetchone()
    event_count = conn.execute(
        "SELECT COUNT(*) FROM noticias WHERE id_evento IS NOT NULL"
    ).fetchone()[0]
    return {
        'date_range': list(date_range),
        'rows_with_event_id': event_count,
        'rows_without_event_id': conn.execute(
            "SELECT COUNT(*) FROM noticias WHERE id_evento IS NULL"
        ).fetchone()[0],
        'sources': [list(r) for r in source_rows],
        'top_tickers': [list(r) for r in ticker_rows],
    }


def audit_news_price_alignment(conn: sqlite3.Connection, sample_limit: int = 5000) -> dict:
    rows = conn.execute(
        """
        SELECT n.id, n.ticker, n.timestamp
        FROM noticias n
        WHERE n.timestamp IS NOT NULL
        ORDER BY n.timestamp DESC
        LIMIT ?
        """,
        (sample_limit,),
    ).fetchall()
    deltas = []
    missing = 0
    for news_id, ticker, ts in rows:
        row = conn.execute(
            """
            SELECT timestamp FROM precios
            WHERE ticker=? AND timestamp>=?
            ORDER BY timestamp ASC LIMIT 1
            """,
            (ticker, ts),
        ).fetchone()
        if not row:
            missing += 1
            continue
        try:
            delta = (parse_ts(row[0]) - parse_ts(ts)).total_seconds()
            deltas.append(delta)
        except ValueError:
            continue
    return {
        'sample_size': len(rows),
        'no_future_price_after_news': missing,
        'median_seconds_to_next_price': sorted(deltas)[len(deltas)//2] if deltas else None,
        'min_seconds_to_next_price': min(deltas) if deltas else None,
        'max_seconds_to_next_price': max(deltas) if deltas else None,
        'negative_alignment_count': sum(1 for x in deltas if x < 0),
    }


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument('db', type=Path)
    parser.add_argument('--output', type=Path, default=Path('data/processed/deep_legacy_audit.json'))
    parser.add_argument('--news-sample', type=int, default=5000)
    args = parser.parse_args()

    conn = sqlite3.connect(args.db)
    try:
        report = {
            'database': str(args.db),
            'prices': audit_prices(conn),
            'news': audit_news(conn),
            'news_price_alignment_sample': audit_news_price_alignment(conn, args.news_sample),
        }
    finally:
        conn.close()

    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding='utf-8')
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nReporte guardado en: {args.output}")
    return 0


if __name__ == '__main__':
    raise SystemExit(main())

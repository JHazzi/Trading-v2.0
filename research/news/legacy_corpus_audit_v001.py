from __future__ import annotations

import argparse
import hashlib
import json
import math
import re
import sqlite3
import statistics
from collections import Counter, defaultdict
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

CONTRACT_VERSION = "legacy_news_causal_audit_v001"

NEWS_TABLE_CANDIDATES = ("noticias", "news", "articles", "noticias_historicas")
PRICE_TABLE_CANDIDATES = ("precios", "prices", "bars", "price_bars")

ALIASES = {
    "ticker": ("ticker", "symbol", "asset_ticker", "simbolo"),
    "timestamp": ("timestamp", "published_at", "published", "datetime", "date", "fecha", "time_published"),
    "title": ("titulo", "title", "headline", "titular"),
    "source": ("fuente", "source", "publisher", "provider"),
    "summary": ("resumen", "summary", "description", "body", "texto"),
    "sentiment": ("sentimiento", "sentiment", "sentiment_score", "score"),
    "url": ("url", "link", "article_url", "canonical_url"),
    "retrieved_at": ("retrieved_at", "first_seen_at", "crawl_date", "crawldate", "ingested_at"),
}

PRICE_ALIASES = {
    "ticker": ("ticker", "symbol", "asset_ticker", "simbolo"),
    "timestamp": ("timestamp", "datetime", "date", "fecha"),
    "close": ("close", "adj_close", "adjusted_close", "cierre"),
}


def qident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def list_tables(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [r[1] for r in conn.execute(f"PRAGMA table_info({qident(table)})")]


def choose_table(tables: Iterable[str], candidates: tuple[str, ...]) -> str | None:
    lookup = {t.casefold(): t for t in tables}
    for c in candidates:
        if c.casefold() in lookup:
            return lookup[c.casefold()]
    return None


def map_columns(cols: list[str], aliases: dict[str, tuple[str, ...]]) -> dict[str, str | None]:
    lookup = {c.casefold(): c for c in cols}
    out = {}
    for logical, names in aliases.items():
        out[logical] = next((lookup[n.casefold()] for n in names if n.casefold() in lookup), None)
    return out


def normalize_title(value: Any) -> str:
    s = str(value or "").casefold()
    s = re.sub(r"https?://\S+", " ", s)
    s = re.sub(r"\b(reuters|bloomberg|ap|associated press|cnn|cnbc|yahoo finance)\b", " ", s)
    s = re.sub(r"[^\w\s]", " ", s, flags=re.UNICODE)
    s = re.sub(r"\s+", " ", s).strip()
    return s


def parse_time(value: Any) -> tuple[datetime | None, bool]:
    if value in (None, ""):
        return None, False
    if isinstance(value, (int, float)):
        try:
            x = float(value)
            if x > 1e12:
                x /= 1000.0
            dt = datetime.fromtimestamp(x, tz=timezone.utc)
            return dt, True
        except Exception:
            return None, False
    text = str(value).strip()
    if not text:
        return None, False

    # ISO first.
    candidate = text.replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(candidate)
        aware = dt.tzinfo is not None and dt.utcoffset() is not None
        return dt, aware
    except Exception:
        pass

    # Common provider formats. These are naive unless the format itself says UTC.
    for fmt in (
        "%Y%m%dT%H%M%S",
        "%Y%m%dT%H%M",
        "%Y-%m-%d %H:%M:%S",
        "%Y-%m-%d %H:%M",
        "%Y-%m-%d",
    ):
        try:
            return datetime.strptime(text, fmt), False
        except ValueError:
            continue
    return None, False


def safe_value(row: sqlite3.Row, col: str | None) -> Any:
    return None if col is None else row[col]


def sample_rows(conn: sqlite3.Connection, table: str, limit: int | None = None):
    sql = f"SELECT * FROM {qident(table)}"
    if limit:
        sql += f" LIMIT {int(limit)}"
    yield from conn.execute(sql)


def distribution(values: Iterable[str], max_items: int = 30) -> dict[str, int]:
    c = Counter(v for v in values if v not in ("", "None", None))
    return dict(c.most_common(max_items))


def infer_price_granularity(conn: sqlite3.Connection, table: str, mapping: dict[str, str | None]) -> dict[str, Any]:
    tc, ts = mapping["ticker"], mapping["timestamp"]
    if not tc or not ts:
        return {"status": "INSUFFICIENT_PRICE_COLUMNS"}
    tickers = [r[0] for r in conn.execute(
        f"SELECT DISTINCT {qident(tc)} FROM {qident(table)} WHERE {qident(tc)} IS NOT NULL LIMIT 12"
    )]
    deltas = []
    aware = 0
    parsed = 0
    for ticker in tickers:
        vals = [r[0] for r in conn.execute(
            f"SELECT {qident(ts)} FROM {qident(table)} "
            f"WHERE {qident(tc)}=? AND {qident(ts)} IS NOT NULL "
            f"ORDER BY {qident(ts)} LIMIT 1000", (ticker,)
        )]
        parsed_times = []
        for v in vals:
            dt, is_aware = parse_time(v)
            if dt is not None:
                parsed += 1
                aware += int(is_aware)
                # normalize aware to UTC-naive only for delta arithmetic
                if is_aware:
                    dt = dt.astimezone(timezone.utc).replace(tzinfo=None)
                parsed_times.append(dt)
        for a, b in zip(parsed_times, parsed_times[1:]):
            sec = (b-a).total_seconds()
            if sec > 0:
                deltas.append(sec)
    if not deltas:
        return {
            "status": "NO_PARSEABLE_DELTAS",
            "parsed_timestamp_count": parsed,
            "timezone_aware_count": aware,
        }
    med = statistics.median(deltas)
    if med <= 120:
        label = "sub_2min_or_minute"
    elif med <= 7200:
        label = "intraday"
    elif med <= 3*86400:
        label = "daily_or_session"
    else:
        label = "sparse"
    return {
        "status": "PASS",
        "median_positive_delta_seconds": med,
        "inferred_granularity": label,
        "parsed_timestamp_count": parsed,
        "timezone_aware_count": aware,
        "timezone_aware_fraction": aware/parsed if parsed else None,
        "note": "Granularity inference is diagnostic only; it does not establish exchange timezone or trading-session semantics.",
    }


def audit(db_path: Path, *, news_table: str | None = None, price_table: str | None = None, example_limit: int = 30) -> dict[str, Any]:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    tables = list_tables(conn)

    news_table = news_table or choose_table(tables, NEWS_TABLE_CANDIDATES)
    price_table = price_table or choose_table(tables, PRICE_TABLE_CANDIDATES)
    if not news_table:
        raise RuntimeError(f"Could not find news table. Available tables: {tables}")

    news_cols = columns(conn, news_table)
    nm = map_columns(news_cols, ALIASES)
    count = int(conn.execute(f"SELECT COUNT(*) FROM {qident(news_table)}").fetchone()[0])

    parsed_times = aware_times = missing_time = 0
    min_time = max_time = None
    normalized_groups: dict[str, list[dict[str, Any]]] = defaultdict(list)
    source_counts = Counter()
    ticker_counts = Counter()
    missing_counts = Counter()
    sentiment_values = []
    url_groups = Counter()
    ticker_title_pairs = Counter()
    cross_ticker_titles: dict[str, set[str]] = defaultdict(set)
    cross_source_titles: dict[str, set[str]] = defaultdict(set)

    essential = ("ticker","timestamp","title","source","summary","sentiment","url","retrieved_at")
    for row in sample_rows(conn, news_table):
        for logical in essential:
            if nm[logical] is None or safe_value(row, nm[logical]) in (None, ""):
                missing_counts[logical] += 1

        ticker = str(safe_value(row, nm["ticker"]) or "").strip().upper()
        title = str(safe_value(row, nm["title"]) or "").strip()
        source = str(safe_value(row, nm["source"]) or "").strip()
        url = str(safe_value(row, nm["url"]) or "").strip()
        sentiment = safe_value(row, nm["sentiment"])
        timestamp = safe_value(row, nm["timestamp"])

        if ticker:
            ticker_counts[ticker] += 1
        if source:
            source_counts[source] += 1
        if sentiment not in (None, ""):
            sentiment_values.append(str(sentiment))
        if url:
            url_groups[url] += 1

        dt, is_aware = parse_time(timestamp)
        if dt is None:
            missing_time += 1
        else:
            parsed_times += 1
            aware_times += int(is_aware)
            cmp_dt = dt.astimezone(timezone.utc).replace(tzinfo=None) if is_aware else dt
            min_time = cmp_dt if min_time is None or cmp_dt < min_time else min_time
            max_time = cmp_dt if max_time is None or cmp_dt > max_time else max_time

        nt = normalize_title(title)
        if nt:
            key = hashlib.sha256(nt.encode("utf-8")).hexdigest()
            ticker_title_pairs[(ticker, key)] += 1
            cross_ticker_titles[key].add(ticker)
            cross_source_titles[key].add(source)
            if len(normalized_groups[key]) < example_limit:
                normalized_groups[key].append({
                    "ticker": ticker,
                    "source": source,
                    "title": title,
                    "timestamp": None if timestamp is None else str(timestamp),
                    "url": url or None,
                })

    dup_title_groups = []
    exact_dup_rows = 0
    for key, examples in normalized_groups.items():
        total = sum(v for (ticker, k), v in ticker_title_pairs.items() if k == key)
        if total > 1:
            exact_dup_rows += total - 1
            dup_title_groups.append({
                "normalized_title_sha256": key,
                "rows": total,
                "distinct_tickers": len(cross_ticker_titles[key] - {""}),
                "distinct_sources": len(cross_source_titles[key] - {""}),
                "examples": examples[:5],
            })
    dup_title_groups.sort(key=lambda x: x["rows"], reverse=True)

    duplicate_url_rows = sum(n-1 for n in url_groups.values() if n > 1)
    multi_ticker_title_groups = sum(1 for k,v in cross_ticker_titles.items() if len(v - {""}) > 1)
    multi_source_title_groups = sum(1 for k,v in cross_source_titles.items() if len(v - {""}) > 1)

    price_info: dict[str, Any]
    if price_table:
        pc = columns(conn, price_table)
        pm = map_columns(pc, PRICE_ALIASES)
        price_count = int(conn.execute(f"SELECT COUNT(*) FROM {qident(price_table)}").fetchone()[0])
        price_info = {
            "table": price_table,
            "rows": price_count,
            "columns": pc,
            "logical_column_map": pm,
            "granularity": infer_price_granularity(conn, price_table, pm),
        }
    else:
        price_info = {"status": "NO_PRICE_TABLE_FOUND"}

    timezone_fraction = aware_times/parsed_times if parsed_times else None
    if nm["timestamp"] is None:
        reaction_gate = "BLOCKED_NO_NEWS_TIMESTAMP"
    elif parsed_times == 0:
        reaction_gate = "BLOCKED_UNPARSEABLE_NEWS_TIME"
    elif timezone_fraction is not None and timezone_fraction < 0.95:
        reaction_gate = "BLOCKED_NEEDS_NEWS_TIMEZONE_CONTRACT"
    elif not price_table:
        reaction_gate = "BLOCKED_NO_PRICE_TABLE"
    else:
        pg = price_info.get("granularity", {})
        if pg.get("status") != "PASS":
            reaction_gate = "BLOCKED_PRICE_TIME_UNCLEAR"
        elif (pg.get("timezone_aware_fraction") or 0) < 0.95:
            reaction_gate = "BLOCKED_NEEDS_PRICE_TIMEZONE_CONTRACT"
        else:
            reaction_gate = "READY_FOR_DESCRIPTIVE_ALIGNMENT_ONLY"

    result = {
        "status": "PASS",
        "contract_version": CONTRACT_VERSION,
        "database": str(db_path),
        "news": {
            "table": news_table,
            "rows": count,
            "columns": news_cols,
            "logical_column_map": nm,
            "timestamp_parse": {
                "parsed": parsed_times,
                "unparsed_or_missing": missing_time,
                "timezone_aware": aware_times,
                "timezone_aware_fraction": timezone_fraction,
                "min_parsed_time": min_time.isoformat() if min_time else None,
                "max_parsed_time": max_time.isoformat() if max_time else None,
            },
            "missing_field_counts": dict(missing_counts),
            "top_sources": dict(source_counts.most_common(30)),
            "top_tickers": dict(ticker_counts.most_common(30)),
            "sentiment_value_sample_distribution": distribution(sentiment_values, 30),
            "dedup_diagnostics": {
                "exact_normalized_title_duplicate_rows": exact_dup_rows,
                "duplicate_canonical_url_rows": duplicate_url_rows,
                "multi_ticker_normalized_title_groups": multi_ticker_title_groups,
                "multi_source_normalized_title_groups": multi_source_title_groups,
                "top_normalized_title_duplicate_groups": dup_title_groups[:example_limit],
                "interpretation": (
                    "Duplicate documents must not become duplicate shocks. Cross-source repetition may still "
                    "carry attention/corroboration information and should be preserved as document evidence."
                ),
            },
        },
        "prices": price_info,
        "reaction_alignment_gate": reaction_gate,
        "causal_time_contract_required": {
            "event_occurrence_at": "Underlying world event time; may be unknown or retrospectively reconstructed.",
            "published_at": "Publisher-stated article time. It is not automatically event occurrence or system availability.",
            "first_seen_at": "First time our provider/system observed the document.",
            "available_at": "Earliest time the predictor may use the evidence under its actual acquisition path.",
            "reaction_start_at": "Outcome diagnostic estimated after the fact; never a predictor feature.",
            "scheduled_for": "Future event date/window known at observation time, with explicit precision.",
        },
        "scientific_warnings": [
            "Do not correlate article timestamp directly with returns until timezone and market-session contracts are explicit.",
            "Do not select only news followed by large moves; no-reaction news are necessary negative/control observations.",
            "Do not treat provider sentiment as impact or return direction.",
            "Do not delete duplicates; cluster them into one story while preserving repetition, source diversity and propagation.",
            "Historical documents retrieved today cannot be relabeled strict-PIT solely from publisher timestamps.",
            "An underlying event may precede the first article and price reaction may precede publication.",
            "Overnight gaps should initially be attributed to the full evidence set between prior close and next open, not one arbitrary article.",
        ],
    }
    conn.close()
    return result


def main() -> None:
    ap = argparse.ArgumentParser(description="Audit legacy news corpus before causal market-reaction modeling.")
    ap.add_argument("--db", default="data/database/market_data.db")
    ap.add_argument("--news-table")
    ap.add_argument("--price-table")
    ap.add_argument("--output", default="reports/news/legacy_corpus_audit_v001.json")
    ap.add_argument("--example-limit", type=int, default=30)
    args = ap.parse_args()
    result = audit(
        Path(args.db),
        news_table=args.news_table,
        price_table=args.price_table,
        example_limit=args.example_limit,
    )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")
    print(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CORE = ROOT / "data" / "processed" / "market_daily_v003_core.db"
CANDIDATE_MARKET_DBS = [ROOT / "data" / "market_data.db", ROOT / "data" / "database" / "market_data.db"]


def table_names(conn: sqlite3.Connection) -> list[str]:
    return [r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table' ORDER BY name")]


def cols(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")]


def scalar(conn: sqlite3.Connection, sql: str, params: tuple[Any, ...] = ()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def inspect_core(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return out
    with sqlite3.connect(path) as conn:
        tables = table_names(conn)
        out["tables"] = tables
        if "market_daily_v003_states" in tables:
            c = cols(conn, "market_daily_v003_states")
            out["state_columns"] = c
            out["state_rows"] = scalar(conn, "SELECT COUNT(*) FROM market_daily_v003_states")
            out["state_assets"] = scalar(conn, "SELECT COUNT(DISTINCT asset_id) FROM market_daily_v003_states")
            out["state_days"] = scalar(conn, "SELECT COUNT(DISTINCT trading_day) FROM market_daily_v003_states")
            out["state_min_day"] = scalar(conn, "SELECT MIN(trading_day) FROM market_daily_v003_states")
            out["state_max_day"] = scalar(conn, "SELECT MAX(trading_day) FROM market_daily_v003_states")
        if "market_daily_v003_labels" in tables:
            c = cols(conn, "market_daily_v003_labels")
            out["label_columns"] = c
            if "horizon_sessions" in c:
                out["label_horizons"] = [
                    {"horizon": int(r[0]), "rows": int(r[1])}
                    for r in conn.execute("SELECT horizon_sessions,COUNT(*) FROM market_daily_v003_labels GROUP BY horizon_sessions ORDER BY horizon_sessions")
                ]
    return out


def inspect_market(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {"path": str(path), "exists": path.exists()}
    if not path.exists():
        return out
    with sqlite3.connect(path) as conn:
        tables = table_names(conn)
        out["tables"] = tables
        if "precios" in tables:
            c = cols(conn, "precios")
            out["price_columns"] = c
            out["price_rows"] = scalar(conn, "SELECT COUNT(*) FROM precios")
            out["price_tickers"] = scalar(conn, "SELECT COUNT(DISTINCT ticker) FROM precios") if "ticker" in c else None
            out["price_min_ts"] = scalar(conn, "SELECT MIN(timestamp) FROM precios") if "timestamp" in c else None
            out["price_max_ts"] = scalar(conn, "SELECT MAX(timestamp) FROM precios") if "timestamp" in c else None
            if "timestamp" in c:
                sample = [str(r[0]) for r in conn.execute("SELECT timestamp FROM precios WHERE timestamp IS NOT NULL ORDER BY timestamp DESC LIMIT 5000")]
                out["recent_sample_rows_by_date_top10"] = Counter(x[:10] for x in sample if len(x) >= 10).most_common(10)
    return out


def main() -> None:
    p = argparse.ArgumentParser(description="Read-only temporal data readiness audit")
    p.add_argument("--core-db", default=str(DEFAULT_CORE))
    p.add_argument("--market-db")
    p.add_argument("--output", default=str(ROOT / "reports" / "temporal_readiness_v001.json"))
    args = p.parse_args()
    market = Path(args.market_db) if args.market_db else next((x for x in CANDIDATE_MARKET_DBS if x.exists()), CANDIDATE_MARKET_DBS[0])
    report = {
        "version": "temporal_readiness_v001",
        "read_only": True,
        "core": inspect_core(Path(args.core_db)),
        "market": inspect_market(market),
        "interpretation_rules": {
            "daily_horizons_are_evaluation_anchors_not_final_time_representation": True,
            "intraday_promotion_requires_more_than_existing_short_validation": True,
            "long_horizon_requires_new_labels_up_to_252_sessions": True,
            "coherent_path_requires_joint_temporal_model_not_interpolation": True
        }
    }
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nWROTE {out}")


if __name__ == "__main__":
    main()

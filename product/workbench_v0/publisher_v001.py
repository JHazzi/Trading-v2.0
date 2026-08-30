from __future__ import annotations

import argparse
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {str(r[0]) for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {str(r[1]) for r in conn.execute(f"PRAGMA table_info({table})")}


def load_price_history(db: Path, ticker: str, limit: int) -> tuple[list[dict[str, Any]], float | None]:
    if not db.exists():
        return [], None
    with sqlite3.connect(db) as conn:
        if "precios" not in _tables(conn):
            return [], None
        cols = _columns(conn, "precios")
        if not {"ticker", "timestamp", "close"}.issubset(cols):
            return [], None
        rows = conn.execute(
            "SELECT timestamp,close FROM precios WHERE ticker=? AND close IS NOT NULL ORDER BY timestamp DESC LIMIT ?",
            (ticker, int(limit)),
        ).fetchall()
    rows = list(reversed(rows))
    points = [{"at": str(ts), "value": float(close), "session_phase": "UNKNOWN"} for ts, close in rows]
    return points, (float(rows[-1][1]) if rows else None)


def load_latest_market_state(core_db: Path, ticker: str) -> dict[str, Any]:
    if not core_db.exists():
        return {}
    with sqlite3.connect(core_db) as conn:
        if "market_daily_v003_states" not in _tables(conn):
            return {}
        cols = _columns(conn, "market_daily_v003_states")
        if not {"ticker", "trading_day"}.issubset(cols):
            return {}
        wanted = [c for c in (
            "asset_id", "ticker", "sector", "trading_day", "feature_version",
            "asset_return_1d_pct", "asset_return_5d_pct", "asset_return_20d_pct",
            "asset_vol_5d_pct", "asset_vol_20d_pct", "asset_vol_63d_pct",
            "asset_drawdown_20d_pct", "asset_drawdown_63d_pct", "asset_drawdown_252d_pct",
            "asset_volume_ratio_20d"
        ) if c in cols]
        row = conn.execute(
            f"SELECT {','.join(wanted)} FROM market_daily_v003_states WHERE ticker=? ORDER BY trading_day DESC LIMIT 1",
            (ticker,),
        ).fetchone()
        return {} if row is None else dict(zip(wanted, row))


def load_forecasts(path: Path | None) -> list[dict[str, Any]]:
    if path is None:
        return []
    raw = json.loads(path.read_text(encoding="utf-8"))
    if isinstance(raw, dict) and isinstance(raw.get("forecasts"), list):
        return raw["forecasts"]
    if isinstance(raw, list):
        return raw
    raise ValueError("forecast artifact must be a list or an object with forecasts[]")


def build_state(ticker: str, price_db: Path, core_db: Path, forecast_artifact: Path | None, history_limit: int) -> dict[str, Any]:
    history, last_price = load_price_history(price_db, ticker, history_limit)
    market_state = load_latest_market_state(core_db, ticker)
    forecasts = load_forecasts(forecast_artifact)
    payload = {
        "contract_version": "investment_state_v0",
        "generated_at": datetime.now(timezone.utc).isoformat(),
        "asset": {
            "ticker": ticker,
            "name": ticker,
            "price": last_price,
            "price_as_of": history[-1]["at"] if history else None,
            "sector": market_state.get("sector")
        },
        "forecasts": forecasts,
        "events": [],
        "decision": {
            "status": "INSUFFICIENT_EVIDENCE",
            "evidence_level": "DEVELOPMENTAL" if forecasts else "RESEARCH_ONLY",
            "summary": "Publisher V001 never emits a product BUY/SELL decision."
        },
        "market_state": market_state,
        "temporal_contract": {
            "version": "multi_resolution_time_v001",
            "time_basis": "EXCHANGE_TRADING_TIME",
            "exchange_calendar": "asset-dependent; resolve upstream",
            "heads": [
                {"head_id": "intraday", "kind": "INTRADAY", "status": "RESEARCH_NOT_PUBLISHED"},
                {"head_id": "daily", "kind": "DAILY", "status": "TERMINAL_DISTRIBUTIONS_AVAILABLE_IF_ARTIFACT_SUPPLIED"},
                {"head_id": "long", "kind": "LONG_HORIZON", "status": "NOT_BUILT"}
            ],
            "evaluation_anchors": [
                {"name": "H1", "coordinate": {"kind": "SESSION_CLOSE", "offset_sessions": 1}},
                {"name": "H3", "coordinate": {"kind": "SESSION_CLOSE", "offset_sessions": 3}},
                {"name": "H5", "coordinate": {"kind": "SESSION_CLOSE", "offset_sessions": 5}},
                {"name": "H10", "coordinate": {"kind": "SESSION_CLOSE", "offset_sessions": 10}}
            ]
        },
        "provenance": {
            "publisher_version": "investment_state_publisher_v001",
            "price_db": str(price_db),
            "core_db": str(core_db),
            "forecast_artifact": str(forecast_artifact) if forecast_artifact else None,
            "forecast_values_synthesized": False,
            "trajectory_interpolation_used": False
        }
    }
    if history:
        payload["history"] = {"mode": "PRICE", "points": history}
    return payload


def main() -> None:
    p = argparse.ArgumentParser(description="Publish real observed data to InvestmentState without synthesizing forecasts")
    p.add_argument("--ticker", required=True)
    p.add_argument("--price-db", required=True)
    p.add_argument("--core-db", default="data/processed/market_daily_v003_core.db")
    p.add_argument("--forecast-artifact")
    p.add_argument("--history-limit", type=int, default=500)
    p.add_argument("--output", required=True)
    args = p.parse_args()
    payload = build_state(
        args.ticker.upper(), Path(args.price_db), Path(args.core_db),
        Path(args.forecast_artifact) if args.forecast_artifact else None,
        args.history_limit
    )
    out = Path(args.output)
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(payload, indent=2, ensure_ascii=False), encoding="utf-8")
    print(f"WROTE {out}")
    print(f"ticker={payload['asset']['ticker']} price={payload['asset'].get('price')}")
    print(f"history_points={len(payload.get('history', {}).get('points', []))}")
    print(f"forecasts={len(payload['forecasts'])} synthesized=false")


if __name__ == "__main__":
    main()

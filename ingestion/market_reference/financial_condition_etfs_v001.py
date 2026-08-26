from __future__ import annotations

import argparse
import json
from datetime import date, timedelta
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "market_brain_daily_v0052_financial_conditions.json"


def windows(start: str, end_exclusive: str, chunk_days: int):
    cur = date.fromisoformat(start)
    end = date.fromisoformat(end_exclusive)
    if chunk_days <= 0 or chunk_days > 3660:
        raise ValueError("chunk_days must be 1..3660")
    while cur < end:
        nxt = min(cur + timedelta(days=chunk_days), end)
        yield cur.isoformat(), nxt.isoformat()
        cur = nxt


def acquire(config_path: Path = DEFAULT_CONFIG) -> dict:
    from ingestion.prices.yahoo_daily_v1 import run_pilot

    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    db = ROOT / cfg["main_db"]
    raw_root = ROOT / "data" / "raw"
    start = cfg["date_window"]["start"]
    end = cfg["date_window"]["end_exclusive"]
    chunk_days = int(cfg["etf_ingestion"]["chunk_days"])

    result = {"status": "PASS", "symbols": {}, "failures": []}
    for ticker, meta in cfg["reference_assets"].items():
        chunks = []
        try:
            for a, b in windows(start, end, chunk_days):
                out = run_pilot(
                    db=db,
                    raw_root=raw_root,
                    ticker=ticker,
                    requested_start=a,
                    requested_end=b,
                    exchange_override=meta["exchange"],
                    max_days=chunk_days,
                )
                chunks.append({
                    "start": a,
                    "end_exclusive": b,
                    "raw_batch_id": out["raw_batch_id"],
                    "bars_discovered": int(out["bars_discovered"]),
                    "bars_inserted": int(out["bars_inserted"]),
                    "bar_observations_inserted": int(out["bar_observations_inserted"]),
                    "quality_run_id": out["quality_run_id"],
                })
            result["symbols"][ticker] = {"status": "PASS", "chunks": chunks}
        except Exception as exc:
            result["status"] = "FAIL"
            result["failures"].append(ticker)
            result["symbols"][ticker] = {
                "status": "FAIL",
                "error_type": type(exc).__name__,
                "error": str(exc),
                "completed_chunks": chunks,
            }
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    a = p.parse_args()
    print(json.dumps(acquire(a.config), indent=2))

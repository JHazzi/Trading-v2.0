from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "market_brain_daily_v0052_financial_conditions.json"


def seed(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    db = ROOT / cfg["main_db"]
    refs = cfg["reference_assets"]
    result = {"status": "PASS", "assets": {}, "failures": []}

    with sqlite3.connect(db) as conn:
        columns = {row[1] for row in conn.execute("PRAGMA table_info(assets)")}
        required = {"asset_id", "ticker", "name", "asset_type", "country", "currency", "exchange", "active", "source"}
        missing = sorted(required - columns)
        if missing:
            raise RuntimeError(f"assets schema missing fields: {missing}")

        for ticker, meta in refs.items():
            row = conn.execute(
                """
                SELECT asset_id,ticker,name,asset_type,exchange,active,source
                FROM assets WHERE UPPER(ticker)=UPPER(?)
                """,
                (ticker,),
            ).fetchone()

            if row is None:
                conn.execute(
                    """
                    INSERT INTO assets(
                        ticker,name,asset_type,sector,industry,country,
                        currency,exchange,active,source
                    ) VALUES (?,?,?,NULL,NULL,'US','USD',?,0,?)
                    """,
                    (ticker, meta["name"], meta["asset_type"], meta["exchange"], "research_financial_conditions_v0052"),
                )
                row = conn.execute(
                    """
                    SELECT asset_id,ticker,name,asset_type,exchange,active,source
                    FROM assets WHERE UPPER(ticker)=UPPER(?)
                    """,
                    (ticker,),
                ).fetchone()
                action = "inserted_reference_inactive"
            else:
                action = "existing_preserved"

            actual_exchange = (row[4] or "").upper().strip()
            expected_exchange = meta["exchange"].upper()
            if actual_exchange != expected_exchange:
                result["failures"].append(f"{ticker}_exchange_{actual_exchange}_expected_{expected_exchange}")

            result["assets"][ticker] = {
                "asset_id": int(row[0]),
                "action": action,
                "asset_type": row[3],
                "exchange": row[4],
                "active": int(row[5]),
                "source": row[6],
            }
        conn.commit()

    if result["failures"]:
        result["status"] = "FAIL"
    return result


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    a = p.parse_args()
    print(json.dumps(seed(a.config), indent=2))

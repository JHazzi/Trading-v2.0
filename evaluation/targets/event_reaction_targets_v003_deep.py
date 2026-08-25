from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path

import numpy as np

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"

LABEL_VERSION = "event_reaction_daily_v0031_deep"
PRICE_ASOF_CONTRACT_VERSION = "daily_price_asof_v1"
PRICE_TRUTH_POLICY = "quality_gated_latest_observation"


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join(str(p) for p in parts).encode()
    return f"{prefix}_{hashlib.sha256(raw).hexdigest()}"


def parse_utc(value: str) -> datetime:
    dt = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if dt.tzinfo is None:
        raise ValueError(f"Timestamp sin timezone: {value}")
    return dt.astimezone(timezone.utc)


@dataclass(frozen=True)
class Bar:
    day: str
    observation_id: str
    version_id: str
    start: str
    end: str
    open: float
    high: float
    low: float
    close: float
    volume: float | None


def _final_series(conn: sqlite3.Connection, asset_id: int) -> list[Bar]:
    rows = conn.execute(
        """
        WITH ranked AS (
            SELECT g.*,
                   ROW_NUMBER() OVER (
                       PARTITION BY g.asset_id, g.trading_day
                       ORDER BY g.observation_sequence DESC,
                                julianday(g.observed_at) DESC,
                                g.price_observation_id DESC
                   ) AS rn
            FROM daily_price_quality_gated_observations_v001 AS g
            WHERE g.asset_id = ?
        )
        SELECT
            trading_day, price_observation_id, price_bar_version_id,
            bar_start_utc, bar_end_utc, open, high, low, close, volume
        FROM ranked
        WHERE rn=1
        ORDER BY trading_day
        """,
        (asset_id,),
    ).fetchall()
    return [
        Bar(
            day=str(r[0]),
            observation_id=str(r[1]),
            version_id=str(r[2]),
            start=str(r[3]),
            end=str(r[4]),
            open=float(r[5]),
            high=float(r[6]),
            low=float(r[7]),
            close=float(r[8]),
            volume=None if r[9] is None else float(r[9]),
        )
        for r in rows
        if all(r[i] is not None for i in (5, 6, 7, 8))
    ]


def _corporate_action_days(
    conn: sqlite3.Connection,
    asset_id: int,
) -> set[str]:
    rows = conn.execute(
        """
        WITH ranked AS (
            SELECT
                o.effective_trading_day,
                o.action_type,
                v.is_present,
                ROW_NUMBER() OVER (
                    PARTITION BY o.asset_id, o.action_type,
                                 o.effective_trading_day
                    ORDER BY o.observation_sequence DESC,
                             julianday(o.observed_at) DESC,
                             o.action_observation_id DESC
                ) AS rn
            FROM corporate_action_observations AS o
            JOIN corporate_action_versions AS v
              ON v.corporate_action_version_id =
                 o.corporate_action_version_id
            WHERE o.asset_id=?
        )
        SELECT effective_trading_day
        FROM ranked
        WHERE rn=1 AND is_present=1
        """,
        (asset_id,),
    ).fetchall()
    return {str(r[0]) for r in rows}


def _label_one(
    bars: list[Bar],
    action_days: set[str],
    state_time: str,
    horizon: int,
    include_intraday_coarse: bool,
) -> dict[str, object]:
    t = parse_utc(state_time)

    # Daily bars cannot isolate the post-event part of an intraday session.
    intraday = any(
        parse_utc(bar.start) <= t < parse_utc(bar.end)
        for bar in bars
    )
    if intraday and not include_intraday_coarse:
        return {
            "status": "intraday_daily_resolution",
            "skip_reason": (
                "event became available during an open session; "
                "daily data cannot isolate post-event reaction"
            ),
        }

    origin_index = None
    for idx, bar in enumerate(bars):
        if parse_utc(bar.end) <= t:
            origin_index = idx
        else:
            break

    if origin_index is None:
        return {
            "status": "insufficient_price_history",
            "skip_reason": "no completed session exists before event state",
        }

    target_index = origin_index + horizon
    if target_index >= len(bars):
        return {
            "status": "insufficient_future_sessions",
            "skip_reason": "not enough future quality-gated sessions",
            "origin": bars[origin_index],
        }

    origin = bars[origin_index]
    target = bars[target_index]
    path = bars[origin_index + 1 : target_index + 1]

    overlapped_actions = sorted(
        day
        for day in action_days
        if origin.day < day <= target.day
    )
    if overlapped_actions:
        return {
            "status": "corporate_action_overlap",
            "skip_reason": ",".join(overlapped_actions),
            "origin": origin,
            "target": target,
        }

    ret = 100.0 * (target.close / origin.close - 1.0)
    mfe = 100.0 * (max(x.high for x in path) / origin.close - 1.0)
    mae = 100.0 * (min(x.low for x in path) / origin.close - 1.0)

    closes = [origin.close] + [x.close for x in path]
    step_returns = [
        100.0 * (closes[i] / closes[i - 1] - 1.0)
        for i in range(1, len(closes))
        if closes[i - 1] != 0
    ]
    path_vol = (
        float(np.std(step_returns, ddof=0))
        if step_returns
        else 0.0
    )

    return {
        "status": "usable",
        "skip_reason": None,
        "origin": origin,
        "target": target,
        "return_pct": ret,
        "mfe_pct": mfe,
        "mae_pct": mae,
        "realized_path_vol_pct": path_vol,
    }


def build(
    db: Path,
    *,
    feature_version: str = "event_state_v0031_deep",
    horizons: tuple[int, ...] = (1, 3, 5, 10),
    include_intraday_coarse: bool = False,
) -> dict[str, object]:
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.row_factory = sqlite3.Row

        views = {
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='view'"
            )
        }
        if "daily_price_quality_gated_observations_v001" not in views:
            raise RuntimeError("Falta aplicar migración 018")

        states = conn.execute(
            """
            SELECT event_state_id, event_id, asset_id, state_time
            FROM normalized_event_state_snapshots
            WHERE feature_version=?
            ORDER BY julianday(state_time), event_state_id
            """,
            (feature_version,),
        ).fetchall()

        series_cache: dict[int, list[Bar]] = {}
        action_cache: dict[int, set[str]] = {}
        inserted = 0
        statuses: dict[str, int] = {}

        for state in states:
            asset_id = int(state["asset_id"])
            if asset_id not in series_cache:
                series_cache[asset_id] = _final_series(conn, asset_id)
                action_cache[asset_id] = _corporate_action_days(
                    conn, asset_id
                )

            bars = series_cache[asset_id]
            actions = action_cache[asset_id]

            for horizon in horizons:
                if horizon not in {1, 3, 5, 10}:
                    raise ValueError(f"Horizonte no soportado: {horizon}")

                result = _label_one(
                    bars,
                    actions,
                    str(state["state_time"]),
                    horizon,
                    include_intraday_coarse,
                )
                status = str(result["status"])
                statuses[status] = statuses.get(status, 0) + 1
                origin = result.get("origin")
                target = result.get("target")
                label_id = stable_id(
                    "erl",
                    state["event_state_id"],
                    horizon,
                    LABEL_VERSION,
                )

                inserted += int(
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO normalized_event_reaction_labels(
                            reaction_label_id, event_state_id, event_id,
                            asset_id, state_time, horizon_sessions,
                            origin_alignment, origin_trading_day,
                            target_trading_day, origin_price_observation_id,
                            target_price_observation_id,
                            origin_price_bar_version_id,
                            target_price_bar_version_id,
                            origin_close, target_close, return_pct,
                            mfe_pct, mae_pct, realized_path_vol_pct,
                            label_status, skip_reason, price_truth_policy,
                            price_asof_contract_version, label_version,
                            metadata_json
                        ) VALUES (
                            ?,?,?,?,?, ?,
                            'last_completed_session_close', ?,?,?,?,?, ?,?,?,?,
                            ?,?,?, ?,?,?,?, ?,?
                        )
                        """,
                        (
                            label_id,
                            state["event_state_id"],
                            state["event_id"],
                            asset_id,
                            state["state_time"],
                            horizon,
                            None if origin is None else origin.day,
                            None if target is None else target.day,
                            None if origin is None else origin.observation_id,
                            None if target is None else target.observation_id,
                            None if origin is None else origin.version_id,
                            None if target is None else target.version_id,
                            None if origin is None else origin.close,
                            None if target is None else target.close,
                            result.get("return_pct"),
                            result.get("mfe_pct"),
                            result.get("mae_pct"),
                            result.get("realized_path_vol_pct"),
                            status,
                            result.get("skip_reason"),
                            PRICE_TRUTH_POLICY,
                            PRICE_ASOF_CONTRACT_VERSION,
                            LABEL_VERSION,
                            json.dumps(
                                {
                                    "intraday_coarse_enabled":
                                        include_intraday_coarse,
                                    "unadjusted_price_truth": True,
                                    "corporate_action_overlap_excluded": True,
                                },
                                sort_keys=True,
                            ),
                        ),
                    ).rowcount
                    == 1
                )

        conn.commit()

    return {
        "feature_version": feature_version,
        "label_version": LABEL_VERSION,
        "states_considered": len(states),
        "labels_inserted": inserted,
        "status_counts": statuses,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument(
        "--feature-version", default="event_state_v0031_deep"
    )
    ap.add_argument(
        "--horizons",
        default="1,3,5,10",
        help="Comma-separated session horizons",
    )
    ap.add_argument(
        "--include-intraday-coarse",
        action="store_true",
        help="Not recommended: daily data cannot isolate intraday reaction.",
    )
    args = ap.parse_args()
    horizons = tuple(int(x) for x in args.horizons.split(",") if x.strip())
    print(
        json.dumps(
            build(
                args.db,
                feature_version=args.feature_version,
                horizons=horizons,
                include_intraday_coarse=args.include_intraday_coarse,
            ),
            indent=2,
            ensure_ascii=False,
        )
    )


if __name__ == "__main__":
    main()

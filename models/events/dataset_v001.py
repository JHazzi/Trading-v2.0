from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

EVENT_FEATURES_NUMERIC = [
    "evidence_count",
    "distinct_cluster_count",
    "distinct_source_count",
    "point_in_time_evidence_fraction",
    "semantic_observed_fact_count",
    "semantic_official_statement_count",
    "semantic_reported_fact_count",
    "semantic_opinion_count",
    "semantic_forecast_count",
    "semantic_rumor_count",
    "semantic_speculation_count",
    "semantic_correction_count",
    "semantic_retraction_count",
    "semantic_mixed_count",
    "semantic_unknown_count",
    "seconds_since_first_evidence",
    "event_age_seconds_filled",
    "time_to_scheduled_seconds_filled",
    "has_known_occurrence_time",
    "has_scheduled_time",
]

EVENT_FEATURES_CATEGORICAL = [
    "event_type",
    "event_subtype_filled",
    "event_scope",
    "source_signature",
]

MARKET_FEATURES = [
    "market_return_1d_pct",
    "market_return_3d_pct",
    "market_return_5d_pct",
    "market_return_10d_pct",
    "market_return_20d_pct",
    "market_vol_5d_pct",
    "market_vol_20d_pct",
    "market_range_1d_pct",
    "market_distance_high_20d_pct",
    "market_distance_low_20d_pct",
    "market_volume_ratio_20d",
]

TARGET = "return_pct"
MARKET_FEATURE_VERSION = "daily_market_context_v001"


def _history_asof(
    conn: sqlite3.Connection,
    asset_id: int,
    state_time: str,
    origin_day: str,
) -> pd.DataFrame:
    # Historical research uses available_at and explicitly permits the
    # session-close backfill assumption. It is not claimed as strict replay.
    rows = conn.execute(
        """
        WITH candidates AS (
            SELECT
                g.trading_day,
                g.close,
                g.high,
                g.low,
                g.volume,
                g.observation_sequence,
                g.observed_at,
                ROW_NUMBER() OVER (
                    PARTITION BY g.asset_id, g.trading_day
                    ORDER BY g.observation_sequence DESC,
                             julianday(g.observed_at) DESC,
                             g.price_observation_id DESC
                ) AS rn
            FROM daily_price_quality_gated_observations_v001 AS g
            WHERE g.asset_id=?
              AND g.trading_day <= ?
              AND julianday(g.available_at) <= julianday(?)
        )
        SELECT trading_day, close, high, low, volume
        FROM candidates
        WHERE rn=1
        ORDER BY trading_day
        """,
        (asset_id, origin_day, state_time),
    ).fetchall()
    return pd.DataFrame(
        rows, columns=["day", "close", "high", "low", "volume"]
    )


def _market_features(history: pd.DataFrame) -> dict[str, float] | None:
    if len(history) < 21:
        return None
    close = history["close"].astype(float).to_numpy()
    high = history["high"].astype(float).to_numpy()
    low = history["low"].astype(float).to_numpy()
    volume = pd.to_numeric(history["volume"], errors="coerce").to_numpy()

    if np.any(~np.isfinite(close[-21:])) or close[-1] == 0:
        return None

    def ret(n: int) -> float:
        return 100.0 * (close[-1] / close[-1 - n] - 1.0)

    daily = 100.0 * (close[1:] / close[:-1] - 1.0)
    last_volume = volume[-1]
    mean_vol = np.nanmean(volume[-20:])
    volume_ratio = (
        float(last_volume / mean_vol)
        if np.isfinite(last_volume)
        and np.isfinite(mean_vol)
        and mean_vol != 0
        else 1.0
    )

    return {
        "market_return_1d_pct": ret(1),
        "market_return_3d_pct": ret(3),
        "market_return_5d_pct": ret(5),
        "market_return_10d_pct": ret(10),
        "market_return_20d_pct": ret(20),
        "market_vol_5d_pct": float(np.std(daily[-5:], ddof=0)),
        "market_vol_20d_pct": float(np.std(daily[-20:], ddof=0)),
        "market_range_1d_pct": float(
            100.0 * (high[-1] - low[-1]) / close[-1]
        ),
        "market_distance_high_20d_pct": float(
            100.0 * (close[-1] / np.max(high[-20:]) - 1.0)
        ),
        "market_distance_low_20d_pct": float(
            100.0 * (close[-1] / np.min(low[-20:]) - 1.0)
        ),
        "market_volume_ratio_20d": volume_ratio,
    }


def load_dataset(
    db: Path,
    horizon_sessions: int,
    *,
    event_feature_version: str = "event_state_v001",
    label_version: str = "event_reaction_daily_v001",
) -> pd.DataFrame:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT
                l.reaction_label_id,
                l.event_state_id,
                l.event_id,
                l.asset_id,
                l.state_time,
                l.origin_trading_day,
                l.return_pct,
                s.*
            FROM normalized_event_reaction_labels AS l
            JOIN normalized_event_state_snapshots AS s
              ON s.event_state_id=l.event_state_id
            WHERE l.horizon_sessions=?
              AND l.label_version=?
              AND l.label_status='usable'
              AND s.feature_version=?
            ORDER BY julianday(l.state_time), l.reaction_label_id
            """,
            (
                horizon_sessions,
                label_version,
                event_feature_version,
            ),
        ).fetchall()

        data = []
        for row in rows:
            history = _history_asof(
                conn,
                int(row["asset_id"]),
                str(row["state_time"]),
                str(row["origin_trading_day"]),
            )
            market = _market_features(history)
            if market is None:
                continue

            item = {
                "reaction_label_id": row["reaction_label_id"],
                "event_state_id": row["event_state_id"],
                "event_id": row["event_id"],
                "asset_id": int(row["asset_id"]),
                "state_time": row["state_time"],
                TARGET: float(row["return_pct"]),
                "event_type": row["event_type"],
                "event_subtype_filled": row["event_subtype"] or "unknown",
                "event_scope": row["event_scope"],
                "source_signature": row["source_signature"],
                "event_age_seconds_filled": (
                    float(row["event_age_seconds"])
                    if row["event_age_seconds"] is not None
                    else 0.0
                ),
                "time_to_scheduled_seconds_filled": (
                    float(row["time_to_scheduled_seconds"])
                    if row["time_to_scheduled_seconds"] is not None
                    else 0.0
                ),
            }
            for name in EVENT_FEATURES_NUMERIC:
                if name in {
                    "event_age_seconds_filled",
                    "time_to_scheduled_seconds_filled",
                }:
                    continue
                item[name] = float(row[name])
            item.update(market)
            data.append(item)

    return pd.DataFrame(data)

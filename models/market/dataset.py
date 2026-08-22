from __future__ import annotations

import sqlite3
from pathlib import Path
import pandas as pd

FEATURE_VERSION = "market_state_v0.1.0"
MIN_COVERAGE = 95.0
TARGET = "return_pct"

FEATURES = [
    "atr_14_pct", "close_location", "distance_high_390m_pct", "distance_low_390m_pct",
    "drawdown_390m_pct", "intrabar_range_pct", "ma_15m_distance_pct", "ma_390m_distance_pct",
    "ma_60m_distance_pct", "realized_vol_30m_pct", "realized_vol_390m_pct",
    "realized_vol_60m_pct", "relative_volume_60m", "return_15m_pct", "return_1m_pct",
    "return_30m_pct", "return_390m_pct", "return_5m_pct", "return_60m_pct", "rsi_14",
    "trend_slope_30m_pct", "trend_slope_390m_pct", "trend_slope_60m_pct",
]


def load_supervised_dataset(
    db_path: Path,
    horizon_seconds: int | None = None,
    min_coverage: float = MIN_COVERAGE,
) -> pd.DataFrame:
    """Build the single canonical supervised dataset.

    Market-state features are stored in EAV form in feature_snapshots, so the
    loader pivots them to one wide row per (asset_id, timestamp) before joining
    them to realized_outcomes. This is the only place where the ML dataset is
    assembled, so training and walk-forward evaluation cannot silently diverge.
    """
    params: list[object] = [FEATURE_VERSION, min_coverage]
    horizon_clause = ""
    if horizon_seconds is not None:
        horizon_clause = "AND ro.horizon_seconds = ?"
        params.append(horizon_seconds)

    feature_columns_sql = ",\n".join(
        f"MAX(CASE WHEN feature_name = '{feature}' THEN feature_value END) AS [{feature}]"
        for feature in FEATURES
    )

    query = f"""
        WITH feature_matrix AS (
            SELECT
                asset_id,
                timestamp,
                {feature_columns_sql}
            FROM feature_snapshots
            WHERE feature_version = ?
            GROUP BY asset_id, timestamp
        )
        SELECT
            ro.outcome_id,
            ro.asset_id,
            ro.origin_time,
            ro.horizon_seconds,
            ro.return_pct,
            ro.mfe_pct,
            ro.mae_pct,
            ro.coverage_pct,
            ro.data_quality,
            {', '.join(f'fm.[{feature}]' for feature in FEATURES)}
        FROM realized_outcomes ro
        INNER JOIN feature_matrix fm
          ON fm.asset_id = ro.asset_id
         AND fm.timestamp = ro.origin_time
        WHERE ro.coverage_pct IS NOT NULL
          AND ro.coverage_pct >= ?
          {horizon_clause}
        ORDER BY ro.origin_time ASC, ro.asset_id ASC
    """

    with sqlite3.connect(db_path) as conn:
        df = pd.read_sql_query(query, conn, params=params)

    if df.empty:
        return df

    df = df.sort_values(["origin_time", "asset_id", "horizon_seconds", "outcome_id"])
    df = df.drop_duplicates(
        ["asset_id", "origin_time", "horizon_seconds"], keep="last"
    ).reset_index(drop=True)

    required = FEATURES + [TARGET]
    missing = [column for column in required if column not in df.columns]
    if missing:
        raise RuntimeError(f"Faltan features del Market State en el dataset: {missing}")

    df = df.dropna(subset=required).reset_index(drop=True)
    return df

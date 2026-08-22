from __future__ import annotations

import argparse
import json
import math
import sqlite3
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
STATE_VERSION = "market_state_v0.1.0"
DEFAULT_INTERVAL = "1m"


@dataclass(frozen=True)
class BuildConfig:
    interval: str = DEFAULT_INTERVAL
    source: str | None = None
    max_assets: int | None = None


def _safe_pct_change(series: pd.Series, periods: int) -> pd.Series:
    return series.pct_change(periods=periods, fill_method=None) * 100.0


def _rolling_slope_pct(series: pd.Series, window: int) -> pd.Series:
    # Approximate local trend as linear slope normalized by current price.
    x = np.arange(window, dtype=float)
    x_mean = x.mean()
    denom = ((x - x_mean) ** 2).sum()

    def calc(values: np.ndarray) -> float:
        if len(values) != window or not np.isfinite(values).all() or values[-1] == 0:
            return np.nan
        y = values.astype(float)
        slope = ((x - x_mean) * (y - y.mean())).sum() / denom
        return (slope / y[-1]) * 100.0

    return series.rolling(window, min_periods=window).apply(calc, raw=True)


def _true_range(df: pd.DataFrame) -> pd.Series:
    prev_close = df["close"].shift(1)
    return pd.concat(
        [
            df["high"] - df["low"],
            (df["high"] - prev_close).abs(),
            (df["low"] - prev_close).abs(),
        ],
        axis=1,
    ).max(axis=1)


def build_asset_state(df: pd.DataFrame) -> pd.DataFrame:
    if df.empty:
        return df

    out = df.copy()
    out["timestamp"] = pd.to_datetime(out["timestamp"], utc=True)
    out = out.sort_values("timestamp").reset_index(drop=True)
    close = out["close"].astype(float)

    # Multi-scale returns: these are state descriptors, not predictions.
    for name, periods in (("return_1m_pct", 1), ("return_5m_pct", 5),
                          ("return_15m_pct", 15), ("return_30m_pct", 30),
                          ("return_60m_pct", 60), ("return_390m_pct", 390)):
        out[name] = _safe_pct_change(close, periods)

    # Trend state: local slope and price-vs-moving-average distances.
    for name, window in (("trend_slope_30m_pct", 30), ("trend_slope_60m_pct", 60),
                         ("trend_slope_390m_pct", 390)):
        out[name] = _rolling_slope_pct(close, window)

    for name, window in (("ma_15m", 15), ("ma_60m", 60), ("ma_390m", 390)):
        ma = close.rolling(window, min_periods=window).mean()
        out[name + "_distance_pct"] = (close / ma - 1.0) * 100.0

    # Volatility: realized std of 1m returns and normalized ATR.
    log_ret = np.log(close / close.shift(1))
    out["realized_vol_30m_pct"] = log_ret.rolling(30, min_periods=30).std() * 100.0
    out["realized_vol_60m_pct"] = log_ret.rolling(60, min_periods=60).std() * 100.0
    out["realized_vol_390m_pct"] = log_ret.rolling(390, min_periods=390).std() * 100.0

    tr = _true_range(out)
    atr_14 = tr.rolling(14, min_periods=14).mean()
    out["atr_14_pct"] = atr_14 / close * 100.0

    # RSI without introducing a prediction.
    delta = close.diff()
    gain = delta.clip(lower=0).rolling(14, min_periods=14).mean()
    loss = (-delta.clip(upper=0)).rolling(14, min_periods=14).mean()
    rs = gain / loss.replace(0, np.nan)
    out["rsi_14"] = 100.0 - (100.0 / (1.0 + rs))

    # Drawdown / distance to rolling extremes.
    high_390 = out["high"].rolling(390, min_periods=1).max()
    low_390 = out["low"].rolling(390, min_periods=1).min()
    out["drawdown_390m_pct"] = (close / high_390 - 1.0) * 100.0
    out["distance_high_390m_pct"] = (close / high_390 - 1.0) * 100.0
    out["distance_low_390m_pct"] = (close / low_390 - 1.0) * 100.0

    # Relative volume. Volume can be null for some providers/assets.
    volume = pd.to_numeric(out.get("volume"), errors="coerce")
    if volume is not None:
        vol_mean = volume.rolling(60, min_periods=20).mean()
        out["relative_volume_60m"] = volume / vol_mean.replace(0, np.nan)
    else:
        out["relative_volume_60m"] = np.nan

    # Candle structure.
    out["intrabar_range_pct"] = (out["high"] - out["low"]) / close * 100.0
    out["close_location"] = (close - out["low"]) / (out["high"] - out["low"]).replace(0, np.nan)

    return out


FEATURE_COLUMNS = [
    "return_1m_pct", "return_5m_pct", "return_15m_pct", "return_30m_pct",
    "return_60m_pct", "return_390m_pct", "trend_slope_30m_pct",
    "trend_slope_60m_pct", "trend_slope_390m_pct", "ma_15m_distance_pct",
    "ma_60m_distance_pct", "ma_390m_distance_pct", "realized_vol_30m_pct",
    "realized_vol_60m_pct", "realized_vol_390m_pct", "atr_14_pct", "rsi_14",
    "drawdown_390m_pct", "distance_high_390m_pct", "distance_low_390m_pct",
    "relative_volume_60m", "intrabar_range_pct", "close_location",
]


def _classify_regime(row: pd.Series) -> str | None:
    vol = row.get("realized_vol_60m_pct")
    slope = row.get("trend_slope_60m_pct")
    if not np.isfinite(vol) or not np.isfinite(slope):
        return None
    # This is explicitly a temporary STATE LABEL, not a trading rule.
    # It is deterministic and will later be replaced/evaluated against learned regimes.
    if vol >= 0.5 and abs(slope) >= 0.01:
        return "volatile_trending"
    if vol >= 0.5:
        return "volatile_range"
    if abs(slope) >= 0.01:
        return "calm_trending"
    return "calm_range"


def write_states(conn: sqlite3.Connection, asset_id: int, state_df: pd.DataFrame) -> int:
    rows_state: list[tuple] = []
    rows_features: list[tuple] = []

    for _, row in state_df.iterrows():
        ts = row["timestamp"].isoformat()
        price = float(row["close"])
        if not math.isfinite(price) or price <= 0:
            continue

        regime = _classify_regime(row)
        trend = row.get("trend_slope_60m_pct")
        vol = row.get("realized_vol_60m_pct")
        liquidity = row.get("relative_volume_60m")
        drawdown = row.get("drawdown_390m_pct")
        d_high = row.get("distance_high_390m_pct")
        d_low = row.get("distance_low_390m_pct")

        rows_state.append((
            asset_id,
            ts,
            price,
            regime,
            _finite_or_none(trend),
            _finite_or_none(vol),
            _finite_or_none(liquidity),
            _finite_or_none(drawdown),
            _finite_or_none(d_high),
            _finite_or_none(d_low),
            None,
            None,
            STATE_VERSION,
        ))

        for feature in FEATURE_COLUMNS:
            rows_features.append((
                asset_id,
                ts,
                feature,
                _finite_or_none(row.get(feature)),
                "market",
                "price_bars",
                STATE_VERSION,
            ))

    conn.executemany(
        """
        INSERT OR REPLACE INTO market_state_snapshots(
            asset_id, timestamp, last_price, market_regime, trend_state,
            volatility_state, liquidity_state, drawdown_pct,
            distance_high_pct, distance_low_pct, sector_strength,
            benchmark_strength, state_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        rows_state,
    )
    conn.executemany(
        """
        INSERT OR REPLACE INTO feature_snapshots(
            asset_id, timestamp, feature_name, feature_value,
            feature_group, source, feature_version
        ) VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        rows_features,
    )
    return len(rows_state)


def _finite_or_none(value) -> float | None:
    try:
        value = float(value)
    except (TypeError, ValueError):
        return None
    return value if math.isfinite(value) else None


def _load_asset_ids(conn: sqlite3.Connection, max_assets: int | None) -> list[int]:
    query = "SELECT asset_id FROM assets WHERE active = 1 ORDER BY asset_id"
    params: tuple = ()
    if max_assets is not None:
        query += " LIMIT ?"
        params = (max_assets,)
    return [row[0] for row in conn.execute(query, params).fetchall()]


def build(db_path: Path, config: BuildConfig) -> dict:
    conn = sqlite3.connect(db_path)
    conn.execute("PRAGMA foreign_keys=ON")
    total_rows = 0
    asset_results = []
    try:
        for asset_id in _load_asset_ids(conn, config.max_assets):
            query = """
                SELECT timestamp, open, high, low, close, volume
                FROM price_bars
                WHERE asset_id = ? AND interval = ?
            """
            params: list = [asset_id, config.interval]
            if config.source:
                query += " AND source = ?"
                params.append(config.source)
            query += " ORDER BY timestamp ASC"
            df = pd.read_sql_query(query, conn, params=params)
            if df.empty:
                continue
            state_df = build_asset_state(df)
            rows = write_states(conn, asset_id, state_df)
            conn.commit()
            total_rows += rows
            asset_results.append({"asset_id": asset_id, "input_rows": len(df), "state_rows": rows})
    finally:
        conn.close()

    return {"state_version": STATE_VERSION, "total_rows": total_rows, "assets": asset_results}


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description="Build deterministic v0.1 market state features")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--interval", default="1m")
    parser.add_argument("--source", default=None)
    parser.add_argument("--max-assets", type=int, default=None)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    result = build(
        args.db,
        BuildConfig(interval=args.interval, source=args.source, max_assets=args.max_assets),
    )
    if args.json:
        print(json.dumps(result, indent=2))
    else:
        print(f"Market State construido: {result['total_rows']} snapshots; "
              f"{len(result['assets'])} activos.")

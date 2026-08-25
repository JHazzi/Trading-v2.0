from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORE_DB = ROOT / "data" / "processed" / "market_daily_v003_core.db"
DEFAULT_OUTPUT_DB = ROOT / "data" / "processed" / "market_daily_v004_math.db"
DEFAULT_CONFIG = ROOT / "config" / "market_brain_daily_v004_math.json"

OWN_PREFIXES = (
    "asset_return_", "asset_vol_", "asset_range_", "asset_volume_", "asset_drawdown_"
)
ASSET_RELATIVE = [
    "asset_minus_cross_section_1d_pct",
    "asset_minus_cross_section_5d_pct",
    "asset_minus_cross_section_20d_pct",
    "asset_minus_sector_1d_pct",
    "asset_minus_sector_5d_pct",
    "asset_minus_sector_20d_pct",
]
MARKET_CONTEXT = [
    "cross_section_mean_return_1d_pct",
    "cross_section_mean_return_5d_pct",
    "cross_section_mean_return_20d_pct",
    "cross_section_breadth_positive_1d",
    "cross_section_breadth_positive_5d",
    "cross_section_dispersion_1d_pct",
    "cross_section_mean_vol_20d_pct",
]
SECTOR_CONTEXT = [
    "sector_mean_return_1d_pct",
    "sector_mean_return_5d_pct",
    "sector_mean_return_20d_pct",
    "sector_breadth_positive_1d",
    "sector_mean_vol_20d_pct",
]


def stable_id(prefix: str, *parts: object) -> str:
    payload = "\0".join(str(x) for x in parts).encode()
    return prefix + "_" + hashlib.sha256(payload).hexdigest()


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg["deferred_until_point_baseline_has_skill"] == []:
        raise ValueError("deferred research layers unexpectedly empty")
    if cfg["research_limitations"]["v003_results_are_retained"] is not True:
        raise ValueError("V003 history must be retained")
    return cfg


def load_core_states(core_db: Path) -> pd.DataFrame:
    with sqlite3.connect(core_db) as conn:
        df = pd.read_sql_query(
            "SELECT * FROM market_daily_v003_states "
            "ORDER BY trading_day, asset_id", conn
        )
    if df.empty:
        raise RuntimeError("core states empty")
    df["trading_day"] = df["trading_day"].astype(str)
    return df


def load_core_labels(core_db: Path) -> pd.DataFrame:
    with sqlite3.connect(core_db) as conn:
        df = pd.read_sql_query(
            """
            SELECT state_id, asset_id, origin_trading_day, target_trading_day,
                   horizon_sessions, return_pct, label_status
            FROM market_daily_v003_labels
            WHERE label_status='usable'
            ORDER BY horizon_sessions, origin_trading_day, asset_id
            """,
            conn,
        )
    if df.empty:
        raise RuntimeError("usable labels empty")
    return df


def _rolling_beta(
    y: pd.Series,
    x: pd.Series,
    group: pd.Series,
    window: int,
) -> pd.Series:
    out = pd.Series(np.nan, index=y.index, dtype=float)
    for _, idx in group.groupby(group, sort=False).groups.items():
        ys = y.loc[idx].astype(float)
        xs = x.loc[idx].astype(float)
        cov = ys.rolling(window, min_periods=window).cov(xs)
        var = xs.rolling(window, min_periods=window).var(ddof=1)
        b = cov / var.where(var > 1e-12)
        out.loc[idx] = b
    return out


def _rolling_std(
    x: pd.Series, group: pd.Series, window: int
) -> pd.Series:
    return (
        x.groupby(group, sort=False)
        .rolling(window, min_periods=window)
        .std(ddof=0)
        .reset_index(level=0, drop=True)
    )


def build_historical_factor_state(states: pd.DataFrame) -> tuple[pd.DataFrame, pd.DataFrame, pd.DataFrame]:
    s = states.copy()

    # Equal-weight realized market return observed by session close.
    market_daily = (
        s.groupby("trading_day", sort=True)["asset_return_1d_pct"]
        .mean()
        .rename("market_return_1d_pct")
        .reset_index()
    )
    s = s.merge(market_daily, on="trading_day", how="left", validate="many_to_one")

    sector_daily = (
        s.groupby(["trading_day", "sector"], sort=True)["asset_return_1d_pct"]
        .mean()
        .rename("sector_return_1d_pct")
        .reset_index()
    )
    s = s.merge(
        sector_daily,
        on=["trading_day", "sector"],
        how="left",
        validate="many_to_one",
    )
    s["sector_factor_1d_pct"] = (
        s["sector_return_1d_pct"] - s["market_return_1d_pct"]
    )

    # Dynamic market beta. Includes information through the current close only.
    for w in (63, 252):
        s[f"beta_market_{w}"] = _rolling_beta(
            s["asset_return_1d_pct"],
            s["market_return_1d_pct"],
            s["asset_id"],
            w,
        )

    # Sector gamma is estimated after removing the contemporaneous market
    # component using the beta available at that date.
    beta_for_hist = s["beta_market_252"].where(
        s["beta_market_252"].notna(), s["beta_market_63"]
    )
    s["_market_residual_1d"] = (
        s["asset_return_1d_pct"]
        - beta_for_hist * s["market_return_1d_pct"]
    )
    for w in (63, 252):
        s[f"gamma_sector_{w}"] = _rolling_beta(
            s["_market_residual_1d"],
            s["sector_factor_1d_pct"],
            s["asset_id"],
            w,
        )
    gamma_for_hist = s["gamma_sector_252"].where(
        s["gamma_sector_252"].notna(), s["gamma_sector_63"]
    )
    s["_idio_residual_1d"] = (
        s["_market_residual_1d"] - gamma_for_hist * s["sector_factor_1d_pct"]
    )
    s["idio_vol_63d_pct"] = _rolling_std(
        s["_idio_residual_1d"], s["asset_id"], 63
    )

    # Day-level state: aggregate true context once per statistical unit.
    m = (
        s.groupby("trading_day", sort=True)
        .agg(
            market_return_1d_pct=("asset_return_1d_pct", "mean"),
            market_return_5d_pct=("cross_section_mean_return_5d_pct", "median"),
            market_return_20d_pct=("cross_section_mean_return_20d_pct", "median"),
            market_breadth_positive_1d=("cross_section_breadth_positive_1d", "median"),
            market_breadth_positive_5d=("cross_section_breadth_positive_5d", "median"),
            market_dispersion_1d_pct=("cross_section_dispersion_1d_pct", "median"),
            market_mean_vol_20d_pct=("cross_section_mean_vol_20d_pct", "median"),
        )
        .reset_index()
    )
    for w in (20, 63):
        m[f"market_realized_vol_{w}d_pct"] = (
            m["market_return_1d_pct"].rolling(w, min_periods=w).std(ddof=0)
        )
        m[f"market_trend_{w}d_pct"] = (
            (1.0 + m["market_return_1d_pct"] / 100.0)
            .rolling(w, min_periods=w)
            .apply(np.prod, raw=True) - 1.0
        ) * 100.0

    synthetic_index = (1.0 + m["market_return_1d_pct"] / 100.0).cumprod()
    for w in (63, 252):
        high = synthetic_index.rolling(w, min_periods=w).max()
        m[f"market_drawdown_{w}d_pct"] = 100.0 * (synthetic_index / high - 1.0)
    m["market_state_id"] = [
        stable_id("market_daily_v004_market_state", d)
        for d in m["trading_day"]
    ]

    # Sector-day state once per statistical unit.
    sec = (
        s.groupby(["trading_day", "sector"], sort=True)
        .agg(
            sector_return_1d_pct=("asset_return_1d_pct", "mean"),
            sector_return_5d_pct=("sector_mean_return_5d_pct", "median"),
            sector_return_20d_pct=("sector_mean_return_20d_pct", "median"),
            sector_breadth_positive_1d=("sector_breadth_positive_1d", "median"),
            sector_mean_vol_20d_pct=("sector_mean_vol_20d_pct", "median"),
            market_return_1d_pct=("market_return_1d_pct", "first"),
            market_return_5d_pct=("cross_section_mean_return_5d_pct", "median"),
            market_return_20d_pct=("cross_section_mean_return_20d_pct", "median"),
        )
        .reset_index()
    )
    for w in (1, 5, 20):
        sec[f"sector_minus_market_{w}d_pct"] = (
            sec[f"sector_return_{w}d_pct"] - sec[f"market_return_{w}d_pct"]
        )
    sec["sector_state_id"] = [
        stable_id("market_daily_v004_sector_state", d, sector)
        for d, sector in zip(sec["trading_day"], sec["sector"])
    ]

    own_cols = [
        c for c in s.columns
        if c.startswith(OWN_PREFIXES)
    ]
    asset_cols = [
        "state_id", "asset_id", "ticker", "sector", "trading_day",
        *own_cols,
        *ASSET_RELATIVE,
        "beta_market_63", "beta_market_252",
        "gamma_sector_63", "gamma_sector_252",
        "idio_vol_63d_pct",
    ]
    asset = s[asset_cols].copy()
    asset["asset_math_state_id"] = [
        stable_id("market_daily_v004_asset_state", sid)
        for sid in asset["state_id"]
    ]
    return m, sec, asset


def build_targets(
    labels: pd.DataFrame,
    asset_state: pd.DataFrame,
    cfg: dict[str, Any],
) -> pd.DataFrame:
    meta = asset_state[
        [
            "state_id", "asset_id", "sector",
            "beta_market_63", "beta_market_252",
            "gamma_sector_63", "gamma_sector_252",
        ]
    ].copy()
    lab = labels.merge(meta, on=["state_id", "asset_id"], how="inner", validate="many_to_one")

    market = (
        lab.groupby(["horizon_sessions", "origin_trading_day"], sort=True)["return_pct"]
        .mean()
        .rename("future_market_return_pct")
        .reset_index()
    )
    lab = lab.merge(
        market,
        on=["horizon_sessions", "origin_trading_day"],
        how="left",
        validate="many_to_one",
    )
    sector = (
        lab.groupby(
            ["horizon_sessions", "origin_trading_day", "sector"], sort=True
        )["return_pct"]
        .mean()
        .rename("future_sector_return_pct")
        .reset_index()
    )
    lab = lab.merge(
        sector,
        on=["horizon_sessions", "origin_trading_day", "sector"],
        how="left",
        validate="many_to_one",
    )

    lab["target_market_additive_pct"] = lab["future_market_return_pct"]
    lab["target_sector_additive_pct"] = (
        lab["future_sector_return_pct"] - lab["future_market_return_pct"]
    )
    lab["target_asset_additive_residual_pct"] = (
        lab["return_pct"] - lab["future_sector_return_pct"]
    )

    beta = lab["beta_market_252"]
    gamma = lab["gamma_sector_252"]
    lab["target_market_beta_component_pct"] = (
        beta * lab["future_market_return_pct"]
    )
    lab["target_sector_beta_component_pct"] = (
        gamma * (
            lab["future_sector_return_pct"] - lab["future_market_return_pct"]
        )
    )
    lab["target_asset_beta_residual_pct"] = (
        lab["return_pct"]
        - lab["target_market_beta_component_pct"]
        - lab["target_sector_beta_component_pct"]
    )
    lab["dynamic_factorization_ready"] = (
        beta.notna() & gamma.notna()
    ).astype(int)

    lab["additive_identity_error"] = (
        lab["return_pct"]
        - lab["target_market_additive_pct"]
        - lab["target_sector_additive_pct"]
        - lab["target_asset_additive_residual_pct"]
    )
    lab["beta_identity_error"] = (
        lab["return_pct"]
        - lab["target_market_beta_component_pct"]
        - lab["target_sector_beta_component_pct"]
        - lab["target_asset_beta_residual_pct"]
    )
    return lab


def build(
    core_db: Path = DEFAULT_CORE_DB,
    output_db: Path = DEFAULT_OUTPUT_DB,
    config_path: Path = DEFAULT_CONFIG,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    states = load_core_states(core_db)
    labels = load_core_labels(core_db)
    market, sector, asset = build_historical_factor_state(states)
    targets = build_targets(labels, asset, cfg)

    output_db.parent.mkdir(parents=True, exist_ok=True)
    if output_db.exists():
        output_db.unlink()

    with sqlite3.connect(output_db) as conn:
        market.to_sql("v004_market_states", conn, index=False, if_exists="replace")
        sector.to_sql("v004_sector_states", conn, index=False, if_exists="replace")
        asset.to_sql("v004_asset_states", conn, index=False, if_exists="replace")
        targets.to_sql("v004_factor_targets", conn, index=False, if_exists="replace")

        conn.executescript("""
        CREATE UNIQUE INDEX idx_v004_market_day
          ON v004_market_states(trading_day);
        CREATE UNIQUE INDEX idx_v004_sector_day
          ON v004_sector_states(trading_day, sector);
        CREATE UNIQUE INDEX idx_v004_asset_state
          ON v004_asset_states(state_id);
        CREATE INDEX idx_v004_target_h_day
          ON v004_factor_targets(horizon_sessions, origin_trading_day);
        CREATE INDEX idx_v004_target_asset_h
          ON v004_factor_targets(asset_id, horizon_sessions, origin_trading_day);
        """)
        metadata = {
            "version": cfg["version"],
            "market_state_rows": int(len(market)),
            "sector_state_rows": int(len(sector)),
            "asset_state_rows": int(len(asset)),
            "target_rows": int(len(targets)),
            "assets": int(asset["asset_id"].nunique()),
            "sectors": int(asset["sector"].nunique()),
            "first_day": str(asset["trading_day"].min()),
            "last_day": str(asset["trading_day"].max()),
        }
        conn.execute(
            "CREATE TABLE build_metadata(key TEXT PRIMARY KEY, value_json TEXT NOT NULL)"
        )
        for k, v in metadata.items():
            conn.execute(
                "INSERT INTO build_metadata VALUES (?,?)",
                (k, json.dumps(v)),
            )
        conn.commit()
    return metadata


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--core-db", type=Path, default=DEFAULT_CORE_DB)
    p.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    a = p.parse_args()
    print(json.dumps(build(a.core_db, a.output_db, a.config), indent=2))

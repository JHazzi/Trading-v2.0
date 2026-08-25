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
DEFAULT_SOURCE_DB = ROOT / "data" / "database" / "market_data_v2.db"
DEFAULT_OUTPUT_DB = ROOT / "data" / "processed" / "market_daily_v003_core.db"
DEFAULT_CONFIG = ROOT / "config" / "market_brain_daily_v003_core.json"
QUALITY_VIEW = "daily_price_quality_gated_observations_v001"

OWN_FEATURES = [
    "asset_return_1d_pct", "asset_return_3d_pct", "asset_return_5d_pct",
    "asset_return_10d_pct", "asset_return_20d_pct", "asset_return_63d_pct",
    "asset_vol_5d_pct", "asset_vol_20d_pct", "asset_vol_63d_pct",
    "asset_range_1d_pct", "asset_volume_ratio_20d",
    "asset_drawdown_20d_pct", "asset_drawdown_63d_pct",
    "asset_drawdown_252d_pct",
]
CROSS_FEATURES = [
    "cross_section_peer_count", "cross_section_mean_return_1d_pct",
    "cross_section_mean_return_5d_pct", "cross_section_mean_return_20d_pct",
    "cross_section_breadth_positive_1d", "cross_section_breadth_positive_5d",
    "cross_section_dispersion_1d_pct", "cross_section_mean_vol_20d_pct",
    "asset_minus_cross_section_1d_pct", "asset_minus_cross_section_5d_pct",
    "asset_minus_cross_section_20d_pct",
]
SECTOR_FEATURES = [
    "sector_peer_count", "sector_context_missing", "sector_mean_return_1d_pct",
    "sector_mean_return_5d_pct", "sector_mean_return_20d_pct",
    "sector_breadth_positive_1d", "sector_mean_vol_20d_pct",
    "asset_minus_sector_1d_pct", "asset_minus_sector_5d_pct",
    "asset_minus_sector_20d_pct",
]
ALL_FEATURES = OWN_FEATURES + CROSS_FEATURES + SECTOR_FEATURES


def stable_id(prefix: str, *parts: object) -> str:
    payload = "\0".join(str(x) for x in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()}"


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if int(cfg["minimum_own_history_days"]) < 253:
        raise ValueError("253 own-history days required")
    if cfg["external_proxies"] or cfg["macro"] or cfg["event_features"]:
        raise ValueError("Core V003 must exclude deferred context")
    if cfg["strict_historical_pit"]:
        raise ValueError("Yahoo historical reconstruction is PIT=0")
    return cfg


def _objects(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {str(r[0]) for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type=?", (kind,)
    )}


def ensure_source_contract(conn: sqlite3.Connection) -> None:
    tables, views = _objects(conn, "table"), _objects(conn, "view")
    required = {"assets", "corporate_action_versions",
                "corporate_action_observations", "daily_price_asof_configs"}
    missing = sorted(required - tables)
    if QUALITY_VIEW not in views:
        missing.append(QUALITY_VIEW)
    if missing:
        raise RuntimeError(f"Missing source objects: {missing}")
    row = conn.execute("""
        SELECT selection_point_in_time_verified, cutoff_column
        FROM daily_price_asof_configs
        WHERE asof_contract_version='daily_price_asof_v1'
          AND mode='historical_session_close_assumption'
    """).fetchone()
    if row is None or int(row[0]) != 0 or str(row[1]) != "available_at":
        raise RuntimeError("Unexpected daily historical as-of contract")


def load_selected_prices(source_db: Path) -> pd.DataFrame:
    with sqlite3.connect(source_db) as conn:
        ensure_source_contract(conn)
        df = pd.read_sql_query(f"""
            WITH eligible AS (
                SELECT g.asset_id, a.ticker, COALESCE(a.sector,'unknown') sector,
                       g.trading_day, g.bar_end_utc state_time,
                       g.open, g.high, g.low, g.close, g.volume,
                       ROW_NUMBER() OVER (
                           PARTITION BY g.asset_id, g.trading_day
                           ORDER BY g.observation_sequence DESC,
                                    julianday(g.observed_at) DESC,
                                    g.price_observation_id DESC
                       ) obs_rank
                FROM {QUALITY_VIEW} g
                JOIN assets a ON a.asset_id=g.asset_id
                WHERE a.active=1 AND a.asset_type='equity' AND g.interval='1d'
                  AND julianday(g.available_at) <= julianday(g.bar_end_utc)
            )
            SELECT asset_id,ticker,sector,trading_day,state_time,
                   open,high,low,close,volume
            FROM eligible WHERE obs_rank=1
            ORDER BY asset_id,trading_day
        """, conn)
    if df.empty:
        raise RuntimeError("No quality-gated causal daily rows")
    for col in ("open", "high", "low", "close", "volume"):
        df[col] = pd.to_numeric(df[col], errors="coerce")
    return df


def compute_own_features(prices: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    df = prices.copy()
    g = df.groupby("asset_id", sort=False)
    df["own_history_days"] = g.cumcount() + 1
    for n in (1,3,5,10,20,63):
        prev = g["close"].shift(n)
        df[f"asset_return_{n}d_pct"] = 100.0 * (df["close"] / prev - 1.0)
    daily = 100.0 * (df["close"] / g["close"].shift(1) - 1.0)
    df["_daily_ret_pct"] = daily
    for n in (5,20,63):
        df[f"asset_vol_{n}d_pct"] = (
            daily.groupby(df["asset_id"], sort=False).rolling(n, min_periods=n)
            .std(ddof=0).reset_index(level=0, drop=True)
        )
    df["asset_range_1d_pct"] = 100.0 * (df["high"] - df["low"]) / df["close"]
    vm = (df["volume"].groupby(df["asset_id"], sort=False)
          .rolling(20, min_periods=20).mean().reset_index(level=0, drop=True))
    df["asset_volume_ratio_20d"] = np.where(
        df["volume"].notna() & (vm > 0), df["volume"] / vm, np.nan
    )
    for n in (20,63,252):
        rh = (df["high"].groupby(df["asset_id"], sort=False)
              .rolling(n, min_periods=n).max().reset_index(level=0, drop=True))
        df[f"asset_drawdown_{n}d_pct"] = 100.0 * (df["close"] / rh - 1.0)
    finite = np.ones(len(df), dtype=bool)
    for col in OWN_FEATURES:
        finite &= np.isfinite(pd.to_numeric(df[col], errors="coerce").to_numpy())
    df["_own_ready"] = (
        (df["own_history_days"] >= int(cfg["minimum_own_history_days"]))
        & finite & (df["close"] > 0)
    )
    return df


def _loo(work: pd.DataFrame, groups: list[str], col: str):
    gb = work.groupby(groups, sort=False)[col]
    count = gb.transform("count").astype(float)
    total = gb.transform("sum")
    sq = (work[col] ** 2).groupby([work[x] for x in groups], sort=False).transform("sum")
    peers = count - 1.0
    mean = (total - work[col]) / peers
    var = ((sq - work[col] ** 2) / peers) - mean ** 2
    return peers, mean, np.sqrt(np.maximum(var, 0.0))


def compute_context_features(df: pd.DataFrame, cfg: dict[str, Any]) -> pd.DataFrame:
    w = df[df["_own_ready"]].copy()
    for col, suffix in (("asset_return_1d_pct","return_1d_pct"),
                        ("asset_return_5d_pct","return_5d_pct"),
                        ("asset_return_20d_pct","return_20d_pct"),
                        ("asset_vol_20d_pct","vol_20d_pct")):
        peers, mean, std = _loo(w, ["trading_day"], col)
        w[f"cross_section_mean_{suffix}"] = mean
        if col == "asset_return_1d_pct":
            w["cross_section_peer_count"] = peers
            w["cross_section_dispersion_1d_pct"] = std
    for col, suffix in (("asset_return_1d_pct","1d"),("asset_return_5d_pct","5d")):
        positive = (w[col] > 0).astype(float)
        total = positive.groupby(w["trading_day"], sort=False).transform("sum")
        n = w.groupby("trading_day", sort=False)[col].transform("count").astype(float)
        w[f"cross_section_breadth_positive_{suffix}"] = (total-positive)/(n-1.0)
    for n in (1,5,20):
        w[f"asset_minus_cross_section_{n}d_pct"] = (
            w[f"asset_return_{n}d_pct"] - w[f"cross_section_mean_return_{n}d_pct"]
        )

    groups = ["trading_day", "sector"]
    for col, suffix in (("asset_return_1d_pct","return_1d_pct"),
                        ("asset_return_5d_pct","return_5d_pct"),
                        ("asset_return_20d_pct","return_20d_pct"),
                        ("asset_vol_20d_pct","vol_20d_pct")):
        peers, mean, _ = _loo(w, groups, col)
        w[f"sector_mean_{suffix}"] = mean
        if col == "asset_return_1d_pct":
            w["sector_peer_count"] = peers
    positive = (w["asset_return_1d_pct"] > 0).astype(float)
    total = positive.groupby([w["trading_day"], w["sector"]], sort=False).transform("sum")
    n = w.groupby(groups, sort=False)["asset_return_1d_pct"].transform("count").astype(float)
    w["sector_breadth_positive_1d"] = (total-positive)/(n-1.0)
    missing = w["sector_peer_count"] < int(cfg["minimum_sector_peers_ex_target"])
    w["sector_context_missing"] = missing.astype(float)
    sector_cols = ["sector_mean_return_1d_pct","sector_mean_return_5d_pct",
                   "sector_mean_return_20d_pct","sector_breadth_positive_1d",
                   "sector_mean_vol_20d_pct"]
    w.loc[missing, sector_cols] = np.nan
    for n in (1,5,20):
        w[f"asset_minus_sector_{n}d_pct"] = np.where(
            missing, np.nan,
            w[f"asset_return_{n}d_pct"] - w[f"sector_mean_return_{n}d_pct"]
        )
    w = w[w["cross_section_peer_count"] >= int(cfg["minimum_cross_section_peers_ex_target"])].copy()
    w["feature_version"] = cfg["feature_version"]
    w["state_point_in_time_verified"] = 0
    w["state_id"] = [stable_id(cfg["feature_version"], int(a), d)
                     for a,d in zip(w["asset_id"],w["trading_day"])]
    return w


def _future(series: pd.Series, asset_ids: pd.Series, h: int, op: str) -> pd.Series:
    """Aggregate the next h sessions, excluding the origin session.

    Path volatility uses population standard deviation (ddof=0).
    For H1 the future path contains one return, so realized path
    volatility is exactly 0 rather than NaN.
    """
    out = pd.Series(index=series.index, dtype=float)
    for _, idx in asset_ids.groupby(asset_ids, sort=False).groups.items():
        s = series.loc[idx]
        rev = s.iloc[::-1]
        rolling = rev.rolling(h, min_periods=h)
        if op == "std":
            roll = rolling.std(ddof=0)
        else:
            roll = getattr(rolling, op)()
        out.loc[idx] = roll.iloc[::-1].shift(-1)
    return out


def load_latest_present_actions(source_db: Path) -> dict[int, np.ndarray]:
    with sqlite3.connect(source_db) as conn:
        rows = conn.execute("""
            WITH ranked AS (
                SELECT o.asset_id,o.effective_trading_day,v.is_present,
                       ROW_NUMBER() OVER (
                         PARTITION BY o.asset_id,o.effective_trading_day,o.action_type
                         ORDER BY o.observation_sequence DESC,julianday(o.observed_at) DESC,
                                  o.action_observation_id DESC
                       ) rn
                FROM corporate_action_observations o
                JOIN corporate_action_versions v
                  ON v.corporate_action_version_id=o.corporate_action_version_id
            )
            SELECT asset_id,effective_trading_day FROM ranked
            WHERE rn=1 AND is_present=1 ORDER BY asset_id,effective_trading_day
        """).fetchall()
    out: dict[int,list[str]] = {}
    for aid, day in rows:
        out.setdefault(int(aid), []).append(str(day))
    return {k: np.array(v, dtype=str) for k,v in out.items()}


def compute_labels(full: pd.DataFrame, states: pd.DataFrame,
                   source_db: Path, cfg: dict[str, Any]) -> pd.DataFrame:
    work = full.copy()
    work["state_id"] = [stable_id(cfg["feature_version"], int(a), d)
                        for a,d in zip(work["asset_id"],work["trading_day"])]
    state_ids = set(states["state_id"])
    actions = load_latest_present_actions(source_db)
    daily = 100.0 * (work["close"] / work.groupby("asset_id", sort=False)["close"].shift(1)-1.0)
    results = []
    for h in map(int, cfg["horizons_sessions"]):
        target_close = work.groupby("asset_id", sort=False)["close"].shift(-h)
        target_day = work.groupby("asset_id", sort=False)["trading_day"].shift(-h)
        tmp = pd.DataFrame({
            "state_id": work["state_id"], "asset_id": work["asset_id"].astype(int),
            "origin_trading_day": work["trading_day"], "target_trading_day": target_day,
            "horizon_sessions": h,
            "return_pct": 100.0*(target_close/work["close"]-1.0),
            "mfe_pct": 100.0*(_future(work["high"],work["asset_id"],h,"max")/work["close"]-1.0),
            "mae_pct": 100.0*(_future(work["low"],work["asset_id"],h,"min")/work["close"]-1.0),
            "realized_path_vol_pct": _future(daily,work["asset_id"],h,"std"),
        })
        tmp = tmp[tmp["state_id"].isin(state_ids)].copy().reset_index(drop=True)
        overlap = np.zeros(len(tmp), dtype=int)
        for aid, idx in tmp.groupby("asset_id", sort=False).groups.items():
            arr = actions.get(int(aid))
            if arr is None or len(arr)==0:
                continue
            origins = tmp.loc[idx,"origin_trading_day"].astype(str).to_numpy()
            targets = tmp.loc[idx,"target_trading_day"].fillna("").astype(str).to_numpy()
            left = np.searchsorted(arr, origins, side="right")
            right = np.searchsorted(arr, targets, side="right")
            overlap[np.asarray(idx,dtype=int)] = (right-left>0).astype(int)
        tmp["corporate_action_overlap"] = overlap
        finite = tmp["target_trading_day"].notna()
        for col in ("return_pct","mfe_pct","mae_pct","realized_path_vol_pct"):
            finite &= np.isfinite(pd.to_numeric(tmp[col],errors="coerce"))
        tmp["label_status"] = np.where(~finite,"insufficient_future",
            np.where(tmp["corporate_action_overlap"].astype(bool),"corporate_action_overlap","usable"))
        tmp["label_version"] = cfg["label_version"]
        tmp["label_id"] = [stable_id(cfg["label_version"],sid,h) for sid in tmp["state_id"]]
        results.append(tmp)
    return pd.concat(results, ignore_index=True)


def build(source_db: Path, output_db: Path, config_path: Path=DEFAULT_CONFIG) -> dict[str,Any]:
    cfg = load_config(config_path)
    prices = load_selected_prices(source_db)
    own = compute_own_features(prices, cfg)
    states = compute_context_features(own, cfg)
    labels = compute_labels(own, states, source_db, cfg)
    cols = ["state_id","asset_id","ticker","sector","trading_day","state_time",
            "feature_version","state_point_in_time_verified","own_history_days",*ALL_FEATURES]
    states = states[cols].copy()
    output_db.parent.mkdir(parents=True, exist_ok=True)
    if output_db.exists(): output_db.unlink()
    with sqlite3.connect(output_db) as conn:
        conn.execute("CREATE TABLE build_metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL)")
        states.to_sql("market_daily_v003_states",conn,index=False,chunksize=20000)
        labels.to_sql("market_daily_v003_labels",conn,index=False,chunksize=20000)
        conn.executescript("""
          CREATE UNIQUE INDEX idx_md3_state ON market_daily_v003_states(state_id);
          CREATE INDEX idx_md3_state_day ON market_daily_v003_states(trading_day,asset_id);
          CREATE INDEX idx_md3_state_asset ON market_daily_v003_states(asset_id,trading_day);
          CREATE UNIQUE INDEX idx_md3_label ON market_daily_v003_labels(label_id);
          CREATE INDEX idx_md3_label_hs ON market_daily_v003_labels(horizon_sessions,label_status);
          CREATE INDEX idx_md3_label_state ON market_daily_v003_labels(state_id,horizon_sessions);
        """)
        meta = {"config":cfg,"source_db":str(source_db),"source_selected_price_rows":int(len(prices)),
                "source_assets":int(prices.asset_id.nunique()),"states":int(len(states)),
                "state_assets":int(states.asset_id.nunique()),"state_first_day":str(states.trading_day.min()),
                "state_last_day":str(states.trading_day.max()),"labels":int(len(labels))}
        for k,v in meta.items():
            conn.execute("INSERT INTO build_metadata VALUES(?,?)",(k,json.dumps(v,sort_keys=True)))
        conn.commit()
    return meta


if __name__ == "__main__":
    p=argparse.ArgumentParser(); p.add_argument("--source-db",type=Path,default=DEFAULT_SOURCE_DB)
    p.add_argument("--output-db",type=Path,default=DEFAULT_OUTPUT_DB); p.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    a=p.parse_args(); print(json.dumps(build(a.source_db,a.output_db,a.config),indent=2))

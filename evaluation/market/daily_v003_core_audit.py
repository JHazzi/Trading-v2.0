from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from features.market.daily_v003_core import (
    ALL_FEATURES, DEFAULT_CONFIG, DEFAULT_OUTPUT_DB, DEFAULT_SOURCE_DB, load_config,
)


def _source_assets(source_db: Path):
    with sqlite3.connect(source_db) as conn:
        conn.row_factory = sqlite3.Row
        total = int(conn.execute("SELECT COUNT(*) FROM assets WHERE active=1 AND asset_type='equity'").fetchone()[0])
        rows = conn.execute("""
            SELECT a.asset_id,a.ticker,COALESCE(a.sector,'unknown') sector
            FROM assets a
            LEFT JOIN (SELECT DISTINCT asset_id FROM daily_price_quality_gated_observations_v001) q
              ON q.asset_id=a.asset_id
            WHERE a.active=1 AND a.asset_type='equity' AND q.asset_id IS NULL
            ORDER BY a.ticker
        """).fetchall()
    return total,[dict(r) for r in rows]


def audit(source_db: Path=DEFAULT_SOURCE_DB, output_db: Path=DEFAULT_OUTPUT_DB,
          config_path: Path=DEFAULT_CONFIG) -> dict[str,Any]:
    cfg=load_config(config_path)
    if not output_db.is_file(): return {"status":"FAIL","failures":["output_db_missing"]}
    with sqlite3.connect(output_db) as conn:
        states=pd.read_sql_query("SELECT * FROM market_daily_v003_states",conn)
        labels=pd.read_sql_query("SELECT * FROM market_daily_v003_labels",conn)
    failures=[]; reviews=[]
    if states.empty: failures.append("states_empty")
    if labels.empty: failures.append("labels_empty")
    if states.state_id.duplicated().any(): failures.append("duplicate_state_ids")
    if labels.label_id.duplicated().any(): failures.append("duplicate_label_ids")
    if float(states.cross_section_peer_count.min()) < cfg["minimum_cross_section_peers_ex_target"]:
        failures.append("cross_section_gate_violation")
    nonfinite={}
    for col in ALL_FEATURES:
        if col.startswith("sector_") or col.startswith("asset_minus_sector_"): continue
        n=int((~np.isfinite(pd.to_numeric(states[col],errors="coerce").to_numpy())).sum())
        if n: nonfinite[col]=n
    if nonfinite: failures.append("non_sector_feature_nonfinite")
    sector_missing=states.sector_context_missing.astype(bool)
    sector_missing_rate=float(sector_missing.mean())
    if sector_missing_rate>0.10: reviews.append("sector_context_missing_gt_10pct")
    bad_time=labels[labels.target_trading_day.notna() &
                    (labels.target_trading_day.astype(str)<=labels.origin_trading_day.astype(str))]
    if len(bad_time): failures.append("non_future_target_day")
    usable=labels[labels.label_status=="usable"].copy()
    if len(usable):
        if int((usable.mfe_pct+1e-12 < usable.return_pct).sum()): failures.append("mfe_below_terminal_return")
        if int((usable.mae_pct-1e-12 > usable.return_pct).sum()): failures.append("mae_above_terminal_return")
        if int((usable.realized_path_vol_pct<0).sum()): failures.append("negative_path_vol")
    by_h={}
    expected_horizons={int(x) for x in cfg["horizons_sessions"]}
    observed_horizons={int(x) for x in labels.horizon_sessions.unique()}
    if observed_horizons != expected_horizons:
        failures.append("horizon_set_mismatch")

    for h,g in labels.groupby("horizon_sessions"):
        counts=g.label_status.value_counts().to_dict(); total=len(g)
        usable_n=int(counts.get("usable",0))
        corp=int(counts.get("corporate_action_overlap",0))
        frac=float(corp/total) if total else 0.0
        by_h[str(int(h))]={"rows":int(total),"usable":usable_n,
            "corporate_action_overlap":corp,"corporate_action_overlap_fraction":frac,
            "insufficient_future":int(counts.get("insufficient_future",0))}

        if total and usable_n < 0.50*total:
            failures.append(f"h{int(h)}_usable_fraction_below_50pct")

        if int(h)==1:
            h1=g[g.label_status=="usable"]
            if h1.empty:
                failures.append("h1_has_no_usable_labels")
            else:
                h1_vol=pd.to_numeric(
                    h1.realized_path_vol_pct,errors="coerce"
                ).to_numpy(float)
                if not np.all(np.isfinite(h1_vol)):
                    failures.append("h1_path_vol_nonfinite")
                elif not np.all(np.abs(h1_vol)<=1e-12):
                    failures.append("h1_path_vol_not_zero")

        if int(h)==10 and frac>0.25:
            reviews.append("h10_corporate_action_exclusion_gt_25pct")
    source_total,excluded=_source_assets(source_db)
    if int(states.asset_id.nunique())<450: failures.append("fewer_than_450_state_assets")
    if int(usable.asset_id.nunique())<450: failures.append("fewer_than_450_usable_label_assets")
    status="FAIL" if failures else ("REVIEW" if reviews else "PASS")
    return {
      "status":status,"failures":sorted(set(failures)),"reviews":sorted(set(reviews)),"contract":cfg,
      "source_active_equities":source_total,"source_assets_without_quality_gated_daily_data":excluded,
      "states":{"rows":int(len(states)),"assets":int(states.asset_id.nunique()),
        "first_day":str(states.trading_day.min()),"last_day":str(states.trading_day.max()),
        "min_cross_section_peers":float(states.cross_section_peer_count.min()),
        "median_cross_section_peers":float(states.cross_section_peer_count.median()),
        "sector_context_missing_rows":int(sector_missing.sum()),
        "sector_context_missing_fraction":sector_missing_rate,
        "non_sector_feature_nonfinite":nonfinite,
        "point_in_time_verified_rows":int(states.state_point_in_time_verified.sum()),
        "by_year":states.assign(year=states.trading_day.astype(str).str[:4]).groupby("year").size().astype(int).to_dict(),
        "by_sector":states.groupby("sector").size().astype(int).sort_values(ascending=False).to_dict()},
      "labels":{"rows":int(len(labels)),"usable_rows":int(len(usable)),
        "usable_assets":int(usable.asset_id.nunique()),"by_horizon":by_h},
      "interpretation":"PASS/REVIEW validates the all-asset-day core dataset; Yahoo history remains PIT=0 and current-cohort selection remains survivorship-biased."
    }

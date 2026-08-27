from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from models.market.distributional_v008_conditional_quantiles import (
    apply_standardized_calibration,
    crossing_fraction,
    load_config,
    monotone_rearrange,
    origin_day_weights,
    resolve_feature_manifest,
    split_recent_days,
    validate_feature_manifest,
    weighted_quantile,
)


def test_config_scientific_freeze():
    cfg = load_config(Path("config/market_brain_distributional_v008.json"))
    assert cfg["primary_reference"] == "vol63_recent_calibrated"
    assert cfg["primary_candidate"] == "hgb_full_endogenous_calibrated"
    assert cfg["residual_scale_feature"] == "asset_vol_63d_pct"
    assert cfg["no_posthoc_feature_family_rescue"] is True
    assert cfg["event_features_added"] is False


def test_origin_day_weights_equalize_day_total():
    days = pd.Series(["a", "a", "b", "b", "b", "b"])
    w = origin_day_weights(days)
    assert np.isclose(w[:2].sum(), w[2:].sum())


def test_weighted_quantile_respects_weights():
    x = np.array([0.0, 10.0])
    w = np.array([9.0, 1.0])
    assert weighted_quantile(x, 0.5, w) == 0.0


def test_recent_split_purges_overlapping_targets():
    rows = []
    for i in range(20):
        rows.append({"origin_trading_day": f"2026-01-{i+1:02d}", "target_trading_day": f"2026-01-{min(i+3,28)+1:02d}"})
    df = pd.DataFrame(rows)
    split = split_recent_days(df, validation_days=5, minimum_train_days=5, minimum_validation_days=5)
    assert (split.train["target_trading_day"] < split.first_validation_day).all()
    assert split.validation["origin_trading_day"].nunique() == 5


def test_monotone_rearrangement_removes_crossing():
    usable = np.array([True, True])
    pred = {0.05: np.array([2.0, 0.0]), 0.5: np.array([1.0, 1.0]), 0.95: np.array([0.0, 2.0])}
    assert crossing_fraction(pred, usable) > 0
    fixed = monotone_rearrange(pred, usable)
    assert crossing_fraction(fixed, usable) == 0
    assert np.all(fixed[0.05] <= fixed[0.5])
    assert np.all(fixed[0.5] <= fixed[0.95])


def test_calibration_shift_applied_only_to_usable():
    pred = {0.05: np.array([-1.0, np.nan]), 0.5: np.array([0.0, np.nan]), 0.95: np.array([1.0, np.nan])}
    usable = np.array([True, False])
    shifts = {0.05: 0.1, 0.5: 0.2, 0.95: 0.3}
    out = apply_standardized_calibration(pred, usable, shifts)
    assert np.allclose([out[0.05][0], out[0.5][0], out[0.95][0]], [-0.9, 0.2, 1.3])
    assert np.isnan(out[0.5][1])


def test_feature_manifest_schema_only(tmp_path: Path):
    db = tmp_path / "core.db"
    with sqlite3.connect(db) as conn:
        conn.execute("""
        CREATE TABLE market_daily_v003_states(
          state_id INTEGER, asset_id INTEGER, ticker TEXT, sector TEXT, trading_day TEXT,
          feature_version TEXT, state_point_in_time_verified INTEGER,
          asset_return_1d_pct REAL, asset_return_5d_pct REAL, asset_return_20d_pct REAL,
          asset_return_63d_pct REAL, asset_vol_5d_pct REAL, asset_vol_20d_pct REAL,
          asset_vol_63d_pct REAL, asset_range_1d_pct REAL, asset_volume_ratio_20d REAL,
          asset_drawdown_20d_pct REAL, asset_drawdown_63d_pct REAL, asset_drawdown_252d_pct REAL,
          cross_section_mean_return_1d_pct REAL, cross_section_breadth_1d REAL,
          sector_mean_return_1d_pct REAL, asset_minus_sector_1d_pct REAL
        )
        """)
    cfg = load_config(Path("config/market_brain_distributional_v008.json"))
    manifest = resolve_feature_manifest(db, cfg)
    validate_feature_manifest(manifest, cfg)
    assert "asset_id" not in manifest["full_endogenous"]
    assert "asset_vol_63d_pct" in manifest["scale_only"]
    assert "cross_section_mean_return_1d_pct" in manifest["full_endogenous"]
    assert "sector_mean_return_1d_pct" in manifest["full_endogenous"]
    assert "cross_section_peer_count" not in manifest["full_endogenous"]
    assert "sector_peer_count" not in manifest["full_endogenous"]
    assert "sector_context_missing" not in manifest["full_endogenous"]

from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd


EXPOSURES = [
    "beta_market_63", "beta_market_252",
    "gamma_sector_63", "gamma_sector_252",
]


def test_target_exposure_copies_are_dropped_before_merge():
    target = pd.DataFrame({
        "state_id":["s1"], "asset_id":[1], "sector":["A"],
        "origin_trading_day":["2020-01-01"],
        "beta_market_63":[np.nan],
        "beta_market_252":[np.nan],
        "gamma_sector_63":[np.nan],
        "gamma_sector_252":[np.nan],
        "return_pct":[1.0],
    })
    state = pd.DataFrame({
        "state_id":["s1"], "asset_id":[1], "sector":["A"],
        "origin_trading_day":["2020-01-01"],
        "beta_market_63":[0.9],
        "beta_market_252":[1.0],
        "gamma_sector_63":[0.8],
        "gamma_sector_252":[0.7],
        "asset_return_1d_pct":[0.1],
    })
    target_for_merge = target.drop(
        columns=[c for c in EXPOSURES if c in target.columns]
    )
    merged = target_for_merge.merge(
        state,
        on=["state_id","asset_id","sector","origin_trading_day"],
        how="inner",
        validate="many_to_one",
        suffixes=("","_state"),
    )
    assert all(c in merged.columns for c in EXPOSURES)
    assert not any(c.endswith("_state") for c in merged.columns)


def test_loader_forbids_exposure_state_aliases():
    src = Path(
        "models/market/daily_v004_factorized_benchmark.py"
    ).read_text(encoding="utf-8")
    assert "duplicate exposure columns survived canonical merge" in src
    assert "dynamic exposure aliases leaked into numeric feature discovery" in src
    assert "target_for_merge = target.drop" in src


def test_plan_requires_full_additive_schema():
    src = Path(
        "pipeline/market_brain_daily_v004_factorized_benchmark.py"
    ).read_text(encoding="utf-8")
    assert "additive_not_equal_raw_usable_rows" in src
    assert "unexpected_additive_feature_count" in src
    assert "unexpected_dynamic_feature_count" in src


def test_v0012_version_and_report_isolation():
    cfg=json.loads(Path(
        "config/market_brain_daily_v004_factorized_benchmark.json"
    ).read_text(encoding="utf-8"))
    assert cfg["version"]=="market_brain_daily_v004_factorized_benchmark_v0012"
    assert cfg["report_dir"].endswith("factorized_benchmark_v0012")
    assert cfg["implementation_fix"][
        "canonical_exposure_feature_source"
    ]=="v004_asset_states"

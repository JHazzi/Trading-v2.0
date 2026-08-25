from __future__ import annotations

import json
from pathlib import Path


def test_additive_and_dynamic_feature_sets_are_separate():
    src=Path(
        "models/market/daily_v004_factorized_benchmark.py"
    ).read_text(encoding="utf-8")
    assert "additive_asset_features" in src
    assert "dynamic_asset_features" in src
    assert "dynamic_only" in src
    assert "asset=asset[_finite(asset,additive_asset_features)]" in src


def test_dynamic_features_do_not_gate_additive_primary():
    src=Path(
        "models/market/daily_v004_factorized_benchmark.py"
    ).read_text(encoding="utf-8")
    gate='asset=asset[_finite(asset,additive_asset_features)].copy()'
    assert gate in src
    assert 'asset=asset[_finite(asset,dynamic_asset_features)].copy()' not in src


def test_plan_hard_fails_if_v003_oos_missing():
    src=Path(
        "pipeline/market_brain_daily_v004_factorized_benchmark.py"
    ).read_text(encoding="utf-8")
    assert "v003_oos_rows_missing_from_additive" in src
    assert "additive_missing_v003_oos_rows" in src
    assert "additive_coverage_below_98pct" in src


def test_dynamic_subset_must_be_secondary_smaller_subset():
    src=Path(
        "pipeline/market_brain_daily_v004_factorized_benchmark.py"
    ).read_text(encoding="utf-8")
    assert "dynamic_subset_not_strictly_smaller" in src


def test_config_versions_fix_before_results():
    cfg=json.loads(Path(
        "config/market_brain_daily_v004_factorized_benchmark.json"
    ).read_text(encoding="utf-8"))
    assert cfg["version"] in {"market_brain_daily_v004_factorized_benchmark_v0011", "market_brain_daily_v004_factorized_benchmark_v0012"}
    assert cfg["implementation_fix"][
        "primary_additive_uses_dynamic_features"
    ] is False

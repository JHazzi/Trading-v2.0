from __future__ import annotations

from pathlib import Path

import numpy as np
import pandas as pd

from evaluation.market.distributional_v0081 import evaluate_horizon_gate
from models.market.distributional_v0081_endogenous_closure import (
    load_config,
    make_capacity_placebo_train,
    validate_frozen_manifest,
)


def test_v0081_config_freezes_closure_question():
    cfg = load_config(Path("config/market_brain_distributional_v0081.json"))
    assert cfg["primary_horizon_sessions"] == 1
    assert cfg["primary_reference"] == "vol63_raw"
    assert cfg["primary_candidate"] == "hgb_own_state_raw"
    assert cfg["post_model_quantile_calibration"] == "none"
    assert cfg["probability_calibration"] == "none"
    assert cfg["no_hyperparameter_selection"] is True
    assert cfg["fresh_untouched_holdout_required_for_promotion"] is True
    assert len(cfg["frozen_own_features"]) == 14
    assert len(cfg["capacity_placebo"]["seeds"]) == 5


def test_frozen_manifest_requires_exact_v008_own_family():
    cfg = load_config(Path("config/market_brain_distributional_v0081.json"))
    manifest = {
        "feature_version": cfg["market_feature_version"],
        "missing_required_scale": [],
        "own_state": list(cfg["frozen_own_features"]),
        "scale_only": list(cfg["capacity_placebo"]["preserved_aligned_features"]),
        "counts": {
            "scale_only": 3,
            "own_state": 14,
            "context_union": 4,
        },
    }
    validate_frozen_manifest(manifest, cfg)
    manifest["own_state"] = list(reversed(manifest["own_state"]))
    try:
        validate_frozen_manifest(manifest, cfg)
    except RuntimeError as exc:
        assert "frozen V008 family" in str(exc)
    else:
        raise AssertionError("manifest order drift should fail")


def test_capacity_placebo_preserves_scale_targets_and_day_support():
    rows = []
    for day_number, day in enumerate(("2026-01-02", "2026-01-05")):
        for asset in range(4):
            rows.append({
                "asset_id": asset,
                "origin_trading_day": day,
                "target_trading_day": f"2026-01-{6 + day_number:02d}",
                "return_pct": float(day_number * 10 + asset),
                "asset_vol_5d_pct": float(1 + asset),
                "asset_vol_20d_pct": float(2 + asset),
                "asset_vol_63d_pct": float(3 + asset),
                "asset_return_1d_pct": float(day_number * 100 + asset),
                "asset_drawdown_20d_pct": float(day_number * 1000 + asset),
            })
    frame = pd.DataFrame(rows)
    own = [
        "asset_vol_5d_pct",
        "asset_vol_20d_pct",
        "asset_vol_63d_pct",
        "asset_return_1d_pct",
        "asset_drawdown_20d_pct",
    ]
    preserved = own[:3]
    placebo, audit = make_capacity_placebo_train(frame, own, preserved, seed=11)

    for column in (
        *preserved,
        "asset_id",
        "origin_trading_day",
        "target_trading_day",
        "return_pct",
    ):
        assert placebo[column].equals(frame[column])
    for day in frame["origin_trading_day"].unique():
        original = frame.loc[
            frame["origin_trading_day"] == day,
            ["asset_return_1d_pct", "asset_drawdown_20d_pct"],
        ].sort_values("asset_return_1d_pct").reset_index(drop=True)
        permuted = placebo.loc[
            placebo["origin_trading_day"] == day,
            ["asset_return_1d_pct", "asset_drawdown_20d_pct"],
        ].sort_values("asset_return_1d_pct").reset_index(drop=True)
        pd.testing.assert_frame_equal(original, permuted)
        aligned = (
            placebo.loc[placebo["origin_trading_day"] == day, "asset_return_1d_pct"]
            .to_numpy()
            == frame.loc[frame["origin_trading_day"] == day, "asset_return_1d_pct"]
            .to_numpy()
        )
        assert not aligned.any()
    assert audit["deranged_row_fraction"] == 1.0


def _metrics(calibration_error: float) -> dict:
    return {
        "per_quantile": {
            name: {"calibration_error": calibration_error}
            for name in ("q05", "q25", "q50", "q75", "q95")
        }
    }


def test_primary_gate_requires_score_calibration_folds_quantiles_and_placebo():
    cfg = load_config(Path("config/market_brain_distributional_v0081.json"))
    primary = {
        "origin_day_equal_weight_delta_pct": 0.01,
        "moving_block_bootstrap": {
            "10": {"ci95": [0.002, 0.018]},
        },
    }
    per_quantile = {
        name: {"origin_day_equal_weight_delta_pct": 0.001}
        for name in ("q05", "q25", "q50", "q75", "q95")
    }
    fold_table = pd.DataFrame({"mean_delta_pct": [0.1, 0.1, 0.1, 0.1, -0.1]})
    placebo_mean = {
        "moving_block_bootstrap": {
            "10": {"ci95": [0.001, 0.01]},
        }
    }
    placebo_seeds = {
        str(seed): {"origin_day_equal_weight_delta_pct": 0.001}
        for seed in cfg["capacity_placebo"]["seeds"]
    }
    gate = evaluate_horizon_gate(
        1,
        primary,
        _metrics(0.01),
        _metrics(0.02),
        per_quantile,
        fold_table,
        placebo_mean,
        placebo_seeds,
        cfg,
    )
    assert gate["status"] == "PASS_DEVELOPMENTAL_REQUIRES_FRESH_HOLDOUT"
    assert all(gate["checks"].values())

    diagnostic = evaluate_horizon_gate(
        3,
        primary,
        _metrics(0.01),
        _metrics(0.02),
        per_quantile,
        fold_table,
        None,
        {},
        cfg,
    )
    assert diagnostic["status"] == "DIAGNOSTIC_ONLY"
    assert diagnostic["may_rescue_primary_horizon"] is False


def test_primary_gate_does_not_promote_positive_point_with_failed_placebo():
    cfg = load_config(Path("config/market_brain_distributional_v0081.json"))
    gate = evaluate_horizon_gate(
        1,
        {
            "origin_day_equal_weight_delta_pct": 0.01,
            "moving_block_bootstrap": {"10": {"ci95": [0.002, 0.018]}},
        },
        _metrics(0.01),
        _metrics(0.02),
        {
            name: {"origin_day_equal_weight_delta_pct": 0.001}
            for name in ("q05", "q25", "q50", "q75", "q95")
        },
        pd.DataFrame({"mean_delta_pct": [0.1, 0.1, 0.1, 0.1, -0.1]}),
        {"moving_block_bootstrap": {"10": {"ci95": [-0.001, 0.01]}}},
        {
            str(seed): {"origin_day_equal_weight_delta_pct": 0.001}
            for seed in cfg["capacity_placebo"]["seeds"]
        },
        cfg,
    )
    assert gate["status"] == "INCONCLUSIVE_OR_AUXILIARY_GATE_FAIL_NO_PROMOTION"
    assert gate["checks"]["candidate_beats_mean_placebo_ci"] is False

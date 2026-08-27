from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluation.market.distributional_v0061_robustness import (
    _assign_regime,
    _leave_one_group_out,
    _scaled_empirical_bundle,
    _weighted_contributions,
    load_config,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "market_brain_distributional_v0061.json"


def test_config_freezes_v006_and_diagnostics_only():
    cfg = load_config(CONFIG)
    assert cfg["source_benchmark_version"] == "market_brain_distributional_v006_baseline_v001"
    assert cfg["primary_candidate"] == "volatility_scaled_empirical"
    assert cfg["primary_scale_feature"] == "asset_vol_20d_pct"
    assert cfg["alternative_scale_features"] == ["asset_vol_5d_pct", "asset_vol_63d_pct"]
    assert cfg["no_new_model_training"] is True
    assert cfg["no_posthoc_candidate_selection"] is True


def test_regime_thresholds_come_from_training_only():
    train = np.arange(1.0, 101.0)
    test = np.array([-999.0, 20.0, 50.0, 80.0, 999.0])
    labels, thresholds = _assign_regime(test, train, [1 / 3, 2 / 3])
    expected = np.quantile(train, [1 / 3, 2 / 3], method="linear")
    np.testing.assert_allclose(thresholds, expected)
    assert list(labels) == ["low", "low", "mid", "high", "high"]


def test_alternative_scale_prediction_does_not_accept_test_outcomes():
    train_y = np.array([-2.0, -1.0, 0.0, 1.0, 2.0])
    train_scale = np.ones(5)
    test_scale = np.array([0.5, 2.0])
    bundle = _scaled_empirical_bundle(
        train_y, train_scale, test_scale, [0.05, 0.5, 0.95]
    )
    width0 = bundle["quantiles"][0.95][0] - bundle["quantiles"][0.05][0]
    width1 = bundle["quantiles"][0.95][1] - bundle["quantiles"][0.05][1]
    assert width1 > width0 > 0.0
    assert np.all((bundle["probability_positive"] >= 0.0) & (bundle["probability_positive"] <= 1.0))


def _synthetic_concentration() -> tuple[pd.DataFrame, np.ndarray]:
    frame = pd.DataFrame({
        "origin_trading_day": [
            "2024-01-01", "2024-01-01", "2024-01-01",
            "2024-01-02", "2024-01-02", "2024-01-02",
        ],
        "asset_id": [1, 2, 3, 1, 2, 3],
        "sector": ["A", "A", "B", "A", "A", "B"],
    })
    delta = np.array([1.0, 2.0, -1.0, 3.0, -2.0, 1.0])
    return frame, delta


def test_weighted_contributions_sum_to_daily_equal_primary():
    frame, delta = _synthetic_concentration()
    table, meta = _weighted_contributions(frame, delta, "asset_id")
    brute = (
        pd.DataFrame({"day": frame["origin_trading_day"], "delta": delta})
        .groupby("day")["delta"]
        .mean()
        .mean()
    )
    assert abs(meta["primary_point_delta_pct"] - brute) < 1e-12
    assert abs(table["weighted_primary_contribution_pct"].sum() - brute) < 1e-12


def test_leave_one_asset_out_matches_brute_force():
    frame, delta = _synthetic_concentration()
    result = _leave_one_group_out(frame, delta, "asset_id").set_index("asset_id")
    for asset_id in sorted(frame["asset_id"].unique()):
        keep = frame["asset_id"].to_numpy() != asset_id
        brute = (
            pd.DataFrame({
                "day": frame.loc[keep, "origin_trading_day"].to_numpy(),
                "delta": delta[keep],
            })
            .groupby("day")["delta"]
            .mean()
            .mean()
        )
        assert abs(result.loc[asset_id, "leave_one_out_primary_delta_pct"] - brute) < 1e-12


def test_leave_one_sector_out_matches_brute_force():
    frame, delta = _synthetic_concentration()
    result = _leave_one_group_out(frame, delta, "sector").set_index("sector")
    for sector in sorted(frame["sector"].unique()):
        keep = frame["sector"].to_numpy() != sector
        brute = (
            pd.DataFrame({
                "day": frame.loc[keep, "origin_trading_day"].to_numpy(),
                "delta": delta[keep],
            })
            .groupby("day")["delta"]
            .mean()
            .mean()
        )
        assert abs(result.loc[sector, "leave_one_out_primary_delta_pct"] - brute) < 1e-12

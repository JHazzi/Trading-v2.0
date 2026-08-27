from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from evaluation.market.daily_v003_benchmark import moving_block_bootstrap_days
from evaluation.market.distributional_v006 import (
    daily_loss_comparison,
    distribution_metrics,
    mean_pinball_rows,
    moving_block_bootstrap_daily_loss,
    pinball_rows,
)
from models.market.distributional_v006_baselines import (
    fit_predict_baselines,
    load_config,
)

ROOT = Path(__file__).resolve().parents[1]
CONFIG = ROOT / "config" / "market_brain_distributional_v006.json"


def _cfg() -> dict[str, object]:
    return json.loads(CONFIG.read_text(encoding="utf-8"))


def _train() -> pd.DataFrame:
    return pd.DataFrame({
        "asset_id": [1, 1, 1, 2, 2, 2, 1, 2],
        "return_pct": [-2.0, 0.0, 2.0, -4.0, 0.0, 4.0, 1.0, -1.0],
        "scale_value": [1.0, 1.0, 1.0, 2.0, 2.0, 2.0, 1.0, 2.0],
    })


def _test(target: float = 100.0) -> pd.DataFrame:
    return pd.DataFrame({
        "asset_id": [1, 2, 999, 1],
        "return_pct": [target, target, target, target],
        "scale_value": [0.5, 2.0, 1.0, 0.0],
    })


def test_config_freezes_versions_and_information_boundary():
    cfg = load_config(CONFIG)
    assert cfg["market_feature_version"] == "market_daily_state_v003_core"
    assert cfg["label_version"] == "market_daily_reaction_v003_core"
    assert cfg["broker_cost_used_for_training"] is False
    assert cfg["event_features_added"] is False
    assert cfg["graph_features_added"] is False
    assert cfg["strict_historical_pit"] is False


def test_pinball_loss_definition():
    actual = np.array([2.0, -2.0])
    prediction = np.array([0.0, 0.0])
    np.testing.assert_allclose(
        pinball_rows(actual, prediction, 0.5),
        np.array([1.0, 1.0]),
    )


def test_distribution_metrics_detect_perfect_median_and_calibration_shape():
    y = np.array([-2.0, -1.0, 1.0, 2.0])
    predictions = {
        0.05: np.array([-3.0] * 4),
        0.25: np.array([-2.0] * 4),
        0.5: y.copy(),
        0.75: np.array([2.0] * 4),
        0.95: np.array([3.0] * 4),
    }
    result = distribution_metrics(y, predictions, np.array([0.5] * 4))
    assert result["median_mae_pct"] == 0.0
    assert result["positive_return_brier"] == 0.25
    assert result["central_90"]["coverage"] == 1.0


def test_quantile_crossing_is_rejected():
    y = np.array([0.0, 1.0])
    with pytest.raises(ValueError, match="crossing"):
        distribution_metrics(
            y,
            {
                0.25: np.array([1.0, 1.0]),
                0.75: np.array([0.0, 0.0]),
            },
            np.array([0.5, 0.5]),
        )


def test_baseline_predictions_do_not_depend_on_test_outcomes():
    cfg = _cfg()
    first = fit_predict_baselines(_train(), _test(100.0), cfg)
    second = fit_predict_baselines(_train(), _test(-100.0), cfg)
    for model in first:
        for q in cfg["quantiles"]:
            np.testing.assert_allclose(
                first[model]["quantiles"][q],
                second[model]["quantiles"][q],
            )
        np.testing.assert_allclose(
            first[model]["probability_positive"],
            second[model]["probability_positive"],
        )


def test_scaled_distribution_width_increases_with_causal_volatility():
    cfg = _cfg()
    predictions = fit_predict_baselines(_train(), _test(), cfg)
    scaled = predictions["volatility_scaled_empirical"]["quantiles"]
    narrow = scaled[0.95][0] - scaled[0.05][0]
    wide = scaled[0.95][1] - scaled[0.05][1]
    assert wide > narrow > 0.0


def test_nonpositive_scale_uses_global_empirical_fallback():
    cfg = _cfg()
    predictions = fit_predict_baselines(_train(), _test(), cfg)
    global_bundle = predictions["train_empirical"]
    scaled_bundle = predictions["volatility_scaled_empirical"]
    for q in cfg["quantiles"]:
        assert scaled_bundle["quantiles"][q][3] == (
            global_bundle["quantiles"][q][3]
        )
    assert scaled_bundle["probability_positive"][3] == (
        global_bundle["probability_positive"][3]
    )


def test_mean_pinball_rows_preserves_row_identity():
    y = np.array([0.0, 2.0])
    predictions = {
        0.25: np.array([0.0, 0.0]),
        0.75: np.array([0.0, 0.0]),
    }
    losses = mean_pinball_rows(y, predictions)
    assert losses.shape == (2,)
    assert losses[0] == 0.0
    assert losses[1] > 0.0


def test_daily_primary_unit_equal_weights_days_not_rows():
    daily = daily_loss_comparison(
        pd.Series(["2024-01-01", "2024-01-01", "2024-01-02"]),
        np.array([2.0, 2.0, 0.0]),
        np.array([1.0, 1.0, 1.0]),
    )
    assert list(daily["rows"]) == [2, 1]
    assert list(daily["loss_delta_baseline_minus_candidate"]) == [1.0, -1.0]
    assert float(daily["loss_delta_baseline_minus_candidate"].mean()) == 0.0


def test_moving_block_bootstrap_is_deterministic_and_reports_sign():
    days = pd.date_range("2024-01-01", periods=20).astype(str)
    daily = pd.DataFrame({
        "origin_trading_day": days,
        "loss_delta_baseline_minus_candidate": np.linspace(0.1, 0.2, 20),
    })
    first = moving_block_bootstrap_daily_loss(
        daily, block_length=5, reps=200, seed=42
    )
    second = moving_block_bootstrap_daily_loss(
        daily, block_length=5, reps=200, seed=42
    )
    assert first == second
    assert first["point_delta_pct"] > 0.0
    assert first["ci95"][0] > 0.0

    canonical = moving_block_bootstrap_days(
        pd.DataFrame({
            "origin_trading_day": days,
            "target": np.zeros(20),
            "baseline": np.linspace(0.1, 0.2, 20),
            "candidate": np.zeros(20),
        }),
        baseline_col="baseline",
        candidate_col="candidate",
        target_col="target",
        block_length=5,
        reps=200,
        seed=42,
    )
    assert first["ci95"] == canonical["ci95"]

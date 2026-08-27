from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd
import pytest

from models.market.distributional_v006_baselines import fit_predict_baselines
from models.market.distributional_v007_adaptive_tail import (
    SideParameters,
    build_inner_temporal_split,
    fit_anchor,
    fit_predict_controls,
    load_config,
    predict_adaptive_distribution,
    probability_positive_from_quantiles,
    select_side_parameters,
    side_multiplier,
)


def _frame(days: int = 800, assets: int = 3) -> pd.DataFrame:
    calendar = pd.date_range("2020-01-01", periods=days, freq="B")
    rows = []
    for j, day in enumerate(calendar):
        for asset in range(assets):
            rows.append({
                "state_id": f"s{asset}_{j}",
                "asset_id": asset + 1,
                "ticker": f"T{asset}",
                "sector": "S",
                "origin_trading_day": day.strftime("%Y-%m-%d"),
                "target_trading_day": (day + pd.offsets.BDay(5)).strftime("%Y-%m-%d"),
                "return_pct": 0.05 * np.sin(j / 11.0) + (asset - 1) * 0.01,
                "asset_vol_20d_pct": 1.0 + 0.2 * (asset + 1) + 0.1 * np.sin(j / 17.0),
                "asset_vol_63d_pct": 1.2 + 0.15 * (asset + 1) + 0.05 * np.sin(j / 41.0),
            })
    return pd.DataFrame(rows)


def _cfg() -> dict:
    path = Path(__file__).resolve().parents[1] / "config" / "market_brain_distributional_v007.json"
    return load_config(path)


def test_config_contract_blocks_deferred_context(tmp_path: Path) -> None:
    cfg = _cfg()
    cfg["event_features_added"] = True
    path = tmp_path / "bad.json"
    path.write_text(json.dumps(cfg), encoding="utf-8")
    with pytest.raises(ValueError):
        load_config(path)


def test_sublinear_alpha_shrinks_scale_response() -> None:
    cfg = _cfg()
    train = _frame(100, 2)
    anchor = fit_anchor(train, cfg["quantiles"])
    test = train.iloc[:4].copy()
    test["asset_vol_20d_pct"] *= 4.0
    test["asset_vol_63d_pct"] *= 4.0
    linear = side_multiplier(test, anchor, SideParameters(1.0, 0.5, 1.0), 3.0)
    sublinear = side_multiplier(test, anchor, SideParameters(0.5, 0.5, 1.0), 3.0)
    assert np.all(sublinear > 1.0)
    assert np.all(sublinear < linear)


def test_asymmetric_prediction_is_ordered() -> None:
    cfg = _cfg()
    train = _frame(200, 3)
    test = train.iloc[-30:].copy()
    anchor = fit_anchor(train.iloc[:-30], cfg["quantiles"])
    bundle = predict_adaptive_distribution(
        test,
        anchor,
        SideParameters(alpha=0.25, lambda20=0.25, kappa=0.8),
        SideParameters(alpha=1.0, lambda20=0.75, kappa=1.2),
        cfg,
    )
    matrix = np.column_stack([bundle["quantiles"][q] for q in cfg["quantiles"]])
    assert np.all(np.diff(matrix, axis=1) >= -1e-12)
    assert np.all((bundle["probability_positive"] >= 0.0) & (bundle["probability_positive"] <= 1.0))


def test_inner_split_purges_outcomes_reaching_validation() -> None:
    cfg = _cfg()
    frame = _frame(800, 2)
    train, val, meta = build_inner_temporal_split(frame, cfg)
    first = meta["first_validation_day"]
    assert (train["target_trading_day"] < first).all()
    assert (val["origin_trading_day"] >= first).all()


def test_vol20_control_reproduces_v006_formula() -> None:
    cfg = _cfg()
    train = _frame(300, 3).iloc[:-90].copy()
    test = _frame(300, 3).iloc[-90:].copy()
    controls = fit_predict_controls(train, test, cfg)
    v006_cfg = {
        "quantiles": cfg["quantiles"],
    }
    train_old = train[["asset_id", "return_pct", "asset_vol_20d_pct"]].rename(
        columns={"asset_vol_20d_pct": "scale_value"}
    )
    test_old = test[["asset_id", "asset_vol_20d_pct"]].rename(
        columns={"asset_vol_20d_pct": "scale_value"}
    )
    old = fit_predict_baselines(train_old, test_old, v006_cfg)["volatility_scaled_empirical"]
    new = controls["vol20_scaled_empirical"]
    for q in cfg["quantiles"]:
        np.testing.assert_allclose(new["quantiles"][q], old["quantiles"][q], atol=1e-12)
    np.testing.assert_allclose(new["probability_positive"], old["probability_positive"], atol=1e-12)


def test_probability_positive_interpolation() -> None:
    pred = {
        0.05: np.array([-4.0, 1.0]),
        0.25: np.array([-1.0, 2.0]),
        0.5: np.array([1.0, 3.0]),
        0.75: np.array([3.0, 4.0]),
        0.95: np.array([6.0, 5.0]),
    }
    prob = probability_positive_from_quantiles(pred, [0.05, 0.25, 0.5, 0.75, 0.95])
    assert 0.5 < prob[0] < 0.75
    assert prob[1] == pytest.approx(1.0)


def test_nested_side_selection_returns_grid_member() -> None:
    cfg = _cfg()
    # Reduce the grid only for a fast unit test; production config remains frozen.
    cfg = dict(cfg)
    cfg["alpha_grid"] = [0.0, 0.5]
    cfg["lambda20_grid"] = [0.0, 1.0]
    cfg["kappa_grid"] = [0.8, 1.0]
    frame = _frame(300, 3)
    train = frame.iloc[:700].copy()
    val = frame.iloc[700:].copy()
    params, table = select_side_parameters(train, val, "upper", cfg)
    assert len(table) == 8
    assert params.alpha in {0.0, 0.5}
    assert params.lambda20 in {0.0, 1.0}
    assert params.kappa in {0.8, 1.0}

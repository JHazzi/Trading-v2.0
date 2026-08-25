from __future__ import annotations

import numpy as np
import pandas as pd

from features.market.daily_v004_math import (
    _rolling_beta,
    build_targets,
)


def test_rolling_beta_does_not_look_forward():
    n = 120
    rng = np.random.default_rng(3)
    x = pd.Series(rng.normal(size=n))
    y = pd.Series(1.5*x + rng.normal(scale=.2, size=n))
    g = pd.Series([1]*n)

    a = _rolling_beta(y, x, g, 63)
    y2 = y.copy()
    x2 = x.copy()
    # mutate strictly future observations
    y2.iloc[100:] = 1e6
    x2.iloc[100:] = -1e6
    b = _rolling_beta(y2, x2, g, 63)

    assert np.allclose(
        a.iloc[:100].to_numpy(float),
        b.iloc[:100].to_numpy(float),
        equal_nan=True,
    )


def test_rolling_beta_recovers_linear_exposure():
    rng = np.random.default_rng(4)
    x = pd.Series(rng.normal(size=400))
    y = pd.Series(2.0*x + rng.normal(scale=.05, size=400))
    g = pd.Series([1]*400)
    beta = _rolling_beta(y, x, g, 252)
    assert abs(float(beta.iloc[-1]) - 2.0) < 0.05


def test_additive_and_dynamic_targets_are_exact():
    labels = pd.DataFrame({
        "state_id": ["a","b","c","d"],
        "asset_id": [1,2,3,4],
        "origin_trading_day": ["2020-01-01"]*4,
        "target_trading_day": ["2020-01-02"]*4,
        "horizon_sessions": [1]*4,
        "return_pct": [1.0,3.0,2.0,6.0],
        "label_status": ["usable"]*4,
    })
    asset = pd.DataFrame({
        "state_id": ["a","b","c","d"],
        "asset_id": [1,2,3,4],
        "sector": ["S1","S1","S2","S2"],
        "beta_market_63": [1,1,1,1],
        "beta_market_252": [1.1,.9,1.2,.8],
        "gamma_sector_63": [1,1,1,1],
        "gamma_sector_252": [.8,1.2,.7,1.3],
    })
    out = build_targets(labels, asset, {})
    assert np.max(np.abs(out["additive_identity_error"])) < 1e-12
    ready = out["dynamic_factorization_ready"].astype(bool)
    assert ready.all()
    assert np.max(np.abs(out.loc[ready,"beta_identity_error"])) < 1e-12


def test_market_and_sector_targets_use_future_outcomes_only_as_labels():
    labels = pd.DataFrame({
        "state_id": ["a","b"],
        "asset_id": [1,2],
        "origin_trading_day": ["2020-01-01"]*2,
        "target_trading_day": ["2020-01-02"]*2,
        "horizon_sessions": [1,1],
        "return_pct": [2.0,4.0],
        "label_status": ["usable","usable"],
    })
    asset = pd.DataFrame({
        "state_id": ["a","b"],
        "asset_id": [1,2],
        "sector": ["S","S"],
        "beta_market_63": [1,1],
        "beta_market_252": [1,1],
        "gamma_sector_63": [1,1],
        "gamma_sector_252": [1,1],
    })
    out = build_targets(labels, asset, {})
    assert np.allclose(out["future_market_return_pct"], 3.0)
    assert np.allclose(out["target_sector_additive_pct"], 0.0)
    assert np.allclose(out["target_asset_additive_residual_pct"], [-1,1])


def test_config_keeps_external_information_incremental():
    import json
    from pathlib import Path
    cfg = json.loads(
        Path("config/market_brain_daily_v004_math.json").read_text()
    )
    ladder = cfg["external_state_ladder_after_math_gate"]
    assert ladder["stage_1_market_tradables"]["symbols"] == ["SPY","QQQ","IWM"]
    assert ladder["stage_4_macro_vintage"]["enabled_now"] is False
    assert "event_brain_integration" in cfg["deferred_until_point_baseline_has_skill"]


def test_no_event_features_in_v004_math_builder():
    from pathlib import Path
    src = Path("features/market/daily_v004_math.py").read_text()
    assert "normalized_event" not in src
    assert "event_state" not in src
    assert "news_" not in src

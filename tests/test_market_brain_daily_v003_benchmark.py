from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluation.market.daily_v003_benchmark import (
    build_purged_day_folds,
    metrics,
    moving_block_bootstrap_days,
)
from models.market.daily_v003_benchmark import train_baselines


def synthetic(n_days=900, assets=60, h=10):
    days = pd.bdate_range("2018-01-01", periods=n_days + h + 5)
    rows = []
    for i in range(n_days):
        for a in range(assets):
            rows.append({
                "origin_trading_day": days[i].date().isoformat(),
                "target_trading_day": days[i+h].date().isoformat(),
                "asset_id": a,
                "return_pct": np.sin(i/20+a/7),
                "asset_return_1d_pct": np.cos(i/17+a),
                "asset_return_3d_pct": np.cos(i/19+a),
                "asset_return_5d_pct": np.cos(i/23+a),
                "asset_return_10d_pct": np.cos(i/29+a),
            })
    return pd.DataFrame(rows)


def test_folds_purge_target_horizon_and_group_days():
    df = synthetic()
    folds = build_purged_day_folds(
        df, n_folds=5, initial_fraction=0.30,
        min_train_days=252, min_test_days=60,
    )
    assert len(folds) == 5
    for f in folds:
        train = df.loc[list(f.train_index)]
        test = df.loc[list(f.test_index)]
        assert train.target_trading_day.max() < test.origin_trading_day.min()
        assert set(train.origin_trading_day).isdisjoint(
            set(test.origin_trading_day)
        )


def test_train_only_baselines_do_not_use_test_target():
    df = synthetic(n_days=400, assets=10, h=1)
    train = df.iloc[:2000].copy()
    test = df.iloc[2000:2200].copy()
    pred1 = train_baselines(train, test, 1)
    mutated = test.copy()
    mutated["return_pct"] = 999999.0
    pred2 = train_baselines(train, mutated, 1)
    for key in pred1:
        assert np.allclose(pred1[key], pred2[key])


def test_moving_block_bootstrap_detects_positive_candidate():
    df = synthetic(n_days=150, assets=20, h=1)
    df["base"] = 0.0
    df["cand"] = df["return_pct"] * 0.5
    result = moving_block_bootstrap_days(
        df,
        baseline_col="base",
        candidate_col="cand",
        target_col="return_pct",
        block_length=10,
        reps=200,
        seed=42,
    )
    assert result["point_delta_pct"] > 0
    assert result["ci95"][0] > 0


def test_metrics_direction_counts_zero_prediction_as_wrong():
    y = np.array([1.0, -1.0])
    p = np.array([0.0, 0.0])
    result = metrics(y, p)
    assert result["directional_accuracy"] == 0.0


def test_config_preregisters_primary_and_no_tuning():
    cfg = json.loads(
        Path("config/market_brain_daily_v003_benchmark.json")
        .read_text(encoding="utf-8")
    )
    assert cfg["primary_model"] == "hgb_full"
    assert cfg["primary_baseline"] == "train_median"
    assert cfg["outer_folds"] == 5
    assert cfg["initial_fraction"] == 0.30
    assert cfg["no_hyperparameter_tuning"] is True
    assert cfg["rf_deferred_to_robustness"] is True


def test_no_event_or_external_proxy_features():
    src = Path("models/market/daily_v003_benchmark.py").read_text(
        encoding="utf-8"
    )
    assert "event_state" not in src
    assert "normalized_event" not in src
    assert '"SPY"' not in src
    assert '"VIX"' not in src

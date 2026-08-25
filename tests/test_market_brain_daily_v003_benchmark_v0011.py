from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluation.market.daily_v003_benchmark import build_purged_day_folds
from models.market.daily_v003_benchmark import train_baselines


def frame(days=400, assets=8, h=5):
    dates = pd.bdate_range("2018-01-01", periods=days+h+2)
    rows=[]
    for i in range(days):
        for a in range(assets):
            rows.append({
                "origin_trading_day": dates[i].date().isoformat(),
                "target_trading_day": dates[i+h].date().isoformat(),
                "asset_id": a,
                "return_pct": float((a-3)*0.01 + np.sin(i/20)),
                "asset_return_1d_pct": .1,
                "asset_return_3d_pct": .2,
                "asset_return_5d_pct": .3,
                "asset_return_10d_pct": .4,
            })
    return pd.DataFrame(rows)


def test_asset_train_median_is_train_only():
    x=frame()
    train=x.iloc[:2000].copy()
    test=x.iloc[2000:2200].copy()
    a=train_baselines(train,test,5)
    mutated=test.copy()
    mutated["return_pct"]=99999
    b=train_baselines(train,mutated,5)
    assert "pred_asset_train_median" in a
    assert np.allclose(a["pred_asset_train_median"],
                       b["pred_asset_train_median"])


def test_direction_baselines_exist():
    x=frame()
    p=train_baselines(x.iloc[:2000],x.iloc[2000:2200],5)
    assert set([
        "pred_always_up_direction",
        "pred_always_down_direction",
        "pred_train_majority_direction",
    ]).issubset(p)


def test_hgb_early_stopping_is_explicitly_false():
    cfg=json.loads(Path(
        "config/market_brain_daily_v003_benchmark.json"
    ).read_text())
    for name in ("hgb_own","hgb_own_cross","hgb_full"):
        assert cfg["models"][name]["early_stopping"] is False


def test_plan_exposes_purge_boundary():
    x=frame(days=900,assets=8,h=10)
    folds=build_purged_day_folds(
        x,n_folds=5,initial_fraction=.30,
        min_train_days=252,min_test_days=60,
    )
    for f in folds:
        assert f.latest_train_target_day < f.first_test_day
        assert f.latest_train_origin_day < f.first_test_day
        assert f.purged_pretest_rows > 0


def test_freezer_records_core_hash_and_environment():
    src=Path("tools/freeze_market_v003_benchmark_v0011.py").read_text()
    assert "scikit_learn" in src
    assert "working_tree_clean" in src
    assert "market_daily_v003_core.db" in src
    assert "sha256" in src


def test_v0011_reports_do_not_overwrite_v001():
    src=Path(
        "pipeline/market_brain_daily_benchmark_v003.py"
    ).read_text(encoding="utf-8")
    assert '"benchmark_v0011"' in src

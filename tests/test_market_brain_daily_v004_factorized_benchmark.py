from __future__ import annotations

import json
import numpy as np
import pandas as pd
from pathlib import Path

from evaluation.market.daily_v004_factorized_benchmark import masks, paired


def test_fold_mask_purges_component_target_end():
    x=pd.DataFrame({
        "origin_trading_day":["2020-01-01","2020-01-02","2020-01-03","2020-01-06"],
        "target_end_day":["2020-01-02","2020-01-06","2020-01-07","2020-01-08"]
    })
    b={"first_test_day":"2020-01-06","last_test_day":"2020-01-06"}
    tr,te=masks(x,b)
    assert list(x.index[tr])==[0]
    assert list(x.index[te])==[3]


def test_paired_positive_means_candidate_better():
    x=pd.DataFrame({
        "return_pct":[1.,-2.,3.],
        "base":[0.,0.,0.],
        "cand":[.5,-1.,2.]
    })
    assert paired(x,"base","cand")[
        "mae_delta_baseline_minus_candidate_pct"
    ]>0


def test_config_freezes_primary_and_v003_boundaries():
    cfg=json.loads(Path(
        "config/market_brain_daily_v004_factorized_benchmark.json"
    ).read_text())
    assert cfg["primary_candidate"]=="hgb_additive_reconstruction"
    assert cfg["primary_baseline"]=="v003_fold_train_median"
    assert cfg["same_outer_fold_boundaries_as_v003"] is True
    assert cfg["decision_rules"]["do_not_tune_after_results"] is True


def test_dynamic_beta_is_secondary():
    cfg=json.loads(Path(
        "config/market_brain_daily_v004_factorized_benchmark.json"
    ).read_text())
    assert cfg["dynamic_beta_candidate"]=="hgb_dynamic_beta_reconstruction"
    assert cfg["decision_rules"]["dynamic_beta_is_secondary_not_replacement_if_primary_fails"] is True


def test_source_contains_no_external_or_event_inputs():
    src=Path("models/market/daily_v004_factorized_benchmark.py").read_text()
    assert '"SPY"' not in src
    assert '"VIX"' not in src
    assert "normalized_event" not in src
    assert "news_" not in src

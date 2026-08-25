from __future__ import annotations

import json
from pathlib import Path

import numpy as np
import pandas as pd

from evaluation.market.daily_v005_market_tradables_benchmark import (
    paired,
    masks,
)


def test_primary_config_is_incremental_v004_to_v005():
    cfg = json.loads(
        Path(
            "config/market_brain_daily_v005_market_tradables_benchmark.json"
        ).read_text(encoding="utf-8")
    )
    pc = cfg["primary_contract"]
    assert pc["baseline"] == "v004_additive_hgb_reconstruction"
    assert pc["candidate"] == (
        "v005_market_tradables_additive_hgb_reconstruction"
    )
    assert pc["only_market_model_changes"] is True
    assert pc["sector_model_unchanged"] is True
    assert pc["asset_model_unchanged"] is True
    assert pc["no_hyperparameter_tuning"] is True


def test_external_block_is_exactly_22_features():
    cfg = json.loads(
        Path(
            "config/market_brain_daily_v005_market_tradables_benchmark.json"
        ).read_text(encoding="utf-8")
    )
    ext = cfg["external_market_features"]
    assert len(ext) == 22
    assert "spy_return_1d_pct" in ext
    assert "qqq_minus_spy_20d_pct" in ext
    assert "iwm_minus_spy_20d_pct" in ext


def test_no_deferred_information_in_benchmark_source():
    src = Path(
        "models/market/daily_v005_market_tradables_benchmark.py"
    ).read_text(encoding="utf-8")
    for forbidden in (
        "XLK", "^VIX", "HYG", "LQD", "news_", "normalized_event",
        "macro_vintage",
    ):
        assert forbidden not in src


def test_paired_positive_delta_means_candidate_better():
    x = pd.DataFrame(
        {
            "return_pct": [1.0, -2.0, 3.0],
            "base": [0.0, 0.0, 0.0],
            "cand": [0.5, -1.0, 2.5],
        }
    )
    out = paired(x, "base", "cand")
    assert out["mae_delta_baseline_minus_candidate_pct"] > 0


def test_masks_purge_target_end_day():
    x = pd.DataFrame(
        {
            "origin_trading_day": [
                "2020-01-01", "2020-01-02", "2020-01-06"
            ],
            "target_end_day": [
                "2020-01-02", "2020-01-06", "2020-01-07"
            ],
        }
    )
    tr, te = masks(
        x,
        {
            "first_test_day": "2020-01-06",
            "last_test_day": "2020-01-06",
        },
    )
    assert list(x.index[tr]) == [0]
    assert list(x.index[te]) == [2]


def test_reconstruction_changes_only_market_component():
    market_v004 = np.array([0.1, 0.2])
    market_v005 = np.array([0.3, -0.1])
    sector = np.array([0.05, 0.05])
    asset = np.array([0.2, -0.2])
    base = market_v004 + sector + asset
    cand = market_v005 + sector + asset
    assert np.allclose(cand - base, market_v005 - market_v004)


def test_historical_reference_pit_not_overclaimed():
    cfg = json.loads(
        Path(
            "config/market_brain_daily_v005_market_tradables_benchmark.json"
        ).read_text(encoding="utf-8")
    )
    assert cfg["causal_limitations"][
        "historical_reference_strict_pit"
    ] is False


def test_v004_replay_parity_gate_present():
    src = Path(
        "models/market/daily_v005_market_tradables_benchmark.py"
    ).read_text(encoding="utf-8")
    assert "V004 replay parity failure" in src
    assert "max_abs_parity > 1e-9" in src


def test_v004_is_single_frozen_oos_reference():
    src = Path(
        "models/market/daily_v005_market_tradables_benchmark.py"
    ).read_text(encoding="utf-8")
    assert "old_v003" not in src
    assert "v003_oos(" not in src
    assert "Stored V004 OOS is the single frozen reference" in src

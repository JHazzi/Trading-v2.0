from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from evaluation.market.daily_v003_benchmark_postmortem import (
    _benchmark_summary,
    _target_decomposition,
)
from features.market.daily_v004_factorized_contract import CONTRACT


def fake_benchmark(h: int, delta: float, cross_delta: float):
    def pooled_model(mae, spear):
        return {
            "metrics": {"rows": 100, "mae_pct": mae},
            "cross_section": {
                "daily_spearman_ic": {"mean": spear}
            },
        }
    return {
        "pooled": {
            "train_median": pooled_model(1.0, None),
            "hgb_own": pooled_model(1.01, 0.01),
            "hgb_own_cross": pooled_model(1.2, 0.0),
            "hgb_full": pooled_model(1.3, -0.01),
            "sgd_huber_full": pooled_model(1.05, 0.02),
        },
        "comparisons": {
            "primary_train_median_vs_hgb_full": {
                "mae_delta_baseline_minus_candidate_pct": delta,
                "candidate_abs_error_win_rate": 0.4,
            },
            "hgb_own_vs_own_cross": {
                "mae_delta_baseline_minus_candidate_pct": cross_delta,
            },
            "hgb_own_cross_vs_full": {
                "mae_delta_baseline_minus_candidate_pct": -0.1,
            },
        },
        "primary_moving_block_bootstrap": {
            "5": {"point_delta_pct": delta, "ci95": [delta-0.1, delta-0.01]},
            "10": {"point_delta_pct": delta, "ci95": [delta-0.2, delta-0.02]},
            "20": {"point_delta_pct": delta, "ci95": [delta-0.3, delta-0.03]},
        },
        "fold_results": [
            {"primary_delta": {
                "mae_delta_baseline_minus_candidate_pct": delta
            }}
        ],
    }


def test_summary_rejects_v003_pattern():
    items = {
        h: fake_benchmark(h, -0.1*h, -0.05*h)
        for h in (1,3,5,10)
    }
    result = _benchmark_summary(items)
    assert result["all_horizons_primary_negative"] is True
    assert result["all_primary_block_ci_upper_below_zero"] is True
    assert result["all_horizons_own_to_cross_negative"] is True


def make_core(path: Path):
    with sqlite3.connect(path) as c:
        c.executescript("""
        CREATE TABLE market_daily_v003_states(
            state_id TEXT PRIMARY KEY,
            sector TEXT
        );
        CREATE TABLE market_daily_v003_labels(
            state_id TEXT,
            origin_trading_day TEXT,
            horizon_sessions INTEGER,
            label_status TEXT,
            return_pct REAL
        );
        """)
        rows = [
            ("a1","S1","2020-01-01",1,1.0),
            ("a2","S1","2020-01-01",1,2.0),
            ("a3","S2","2020-01-01",1,5.0),
            ("b1","S1","2020-01-02",1,-1.0),
            ("b2","S1","2020-01-02",1,0.0),
            ("b3","S2","2020-01-02",1,2.0),
        ]
        for sid,sector,day,h,r in rows:
            c.execute(
                "INSERT INTO market_daily_v003_states VALUES(?,?)",
                (sid,sector),
            )
            c.execute(
                """
                INSERT INTO market_daily_v003_labels
                VALUES(?,?,?,'usable',?)
                """,
                (sid,day,h,r),
            )


def test_target_decomposition_is_finite(tmp_path: Path):
    db=tmp_path/"x.db"
    make_core(db)
    r=_target_decomposition(db,1)
    assert r["rows"] == 6
    assert 0 <= r["market_factor_variance_fraction"] <= 1
    assert r["oracle_daily_median_residual_mae_pct"] < r["zero_mae_pct"]


def test_factorized_identity_is_explicit():
    assert CONTRACT["identity"] == (
        "asset_return = market_factor + sector_factor + asset_residual"
    )
    assert CONTRACT["levels"]["market"]["unit"] == "origin_trading_day"
    assert CONTRACT["levels"]["sector"]["unit"] == "origin_trading_day_x_sector"
    assert CONTRACT["levels"]["asset"]["unit"] == "origin_trading_day_x_asset"


def test_no_external_context_before_factorization_gate():
    guards=CONTRACT["scientific_guards"]
    assert guards["external_proxies"] is False
    assert guards["macro"] is False
    assert guards["events"] is False
    assert guards["distributional_training"] is False


def test_docs_close_v003_as_negative_result():
    src=Path(
        "tools/patch_market_v003_results_v004_foundation_docs.py"
    ).read_text(encoding="utf-8")
    assert "REJECTED" in src
    assert "D022" in src
    assert "hypothesis to test" in src.lower()


def test_topology_separates_context_from_asset_relative_features():
    src=Path(
        "evaluation/market/daily_v003_benchmark_postmortem.py"
    ).read_text(encoding="utf-8")
    assert "MARKET_CONTEXT_FEATURES" in src
    assert "ASSET_MARKET_RELATIVE_FEATURES" in src
    assert "SECTOR_CONTEXT_FEATURES" in src
    assert "ASSET_SECTOR_RELATIVE_FEATURES" in src
    assert 'startswith("asset_minus_cross_section_")' in src
    assert 'startswith("asset_minus_sector_")' in src

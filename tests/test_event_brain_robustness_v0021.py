from __future__ import annotations

import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from evaluation.events.robustness_v0021 import (
    BLOCK_LENGTHS,
    RF_SEEDS,
    SIMPLE_FAMILIES,
    attach_accession_numbers,
    build_purged_group_folds,
    moving_block_bootstrap,
    parse_sec_accession_identity,
)
from models.events.robustness_v0021 import (
    _simple_pipeline,
)
from models.events.dataset_v002 import (
    EVENT_FEATURES_CATEGORICAL,
    EVENT_FEATURES_NUMERIC,
    MARKET_CATEGORICAL,
    MARKET_FEATURES,
)


def test_preregistration_constants_are_frozen():
    assert RF_SEEDS == (7, 17, 42, 123, 2026)
    assert SIMPLE_FAMILIES == ("ridge", "elasticnet", "huber")
    assert BLOCK_LENGTHS == (5, 10, 20)


def test_parse_sec_accession_identity():
    assert (
        parse_sec_accession_identity(
            "sec:0000320193-24-000123:item:2.02"
        )
        == "0000320193-24-000123"
    )


def test_attach_accession_uses_stable_identity_contract(tmp_path: Path):
    db = tmp_path / "x.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE normalized_event_identities(
                event_id TEXT PRIMARY KEY,
                identity_method TEXT NOT NULL,
                identity_key TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO normalized_event_identities
            VALUES('e1','sec_accession_item_v001',
                   'sec:0001-24-000001:item:2.02')
            """
        )
    frame = pd.DataFrame({"event_id": ["e1", "e1"]})
    out = attach_accession_numbers(db, frame)
    assert set(out["accession_number"]) == {"0001-24-000001"}


def _synthetic_frame(n: int = 160) -> pd.DataFrame:
    days = pd.bdate_range("2020-01-01", periods=n + 20)
    rows = []
    for i in range(n):
        event_id = f"e{i:03d}"
        accession = f"a{i // 2:03d}"  # two events per filing
        rows.append(
            {
                "event_id": event_id,
                "accession_number": accession,
                "event_anchor_day": days[i].date().isoformat(),
                "origin_trading_day": days[i].date().isoformat(),
                "target_trading_day": days[i + 10].date().isoformat(),
                "asset_id": i % 10,
                "event_type": f"type{i % 5}",
                "state_time": days[i].isoformat(),
                "reaction_label_id": f"r{i}",
                "return_pct": float(np.sin(i / 7.0)),
            }
        )
    return pd.DataFrame(rows)


def test_accession_folds_have_no_accession_or_target_overlap():
    frame = _synthetic_frame()
    folds = build_purged_group_folds(
        frame,
        group_column="accession_number",
        n_folds=4,
        initial_fraction=0.45,
        min_train_rows=30,
        min_test_rows=10,
    )
    assert len(folds) >= 2
    for fold in folds:
        train = frame.loc[list(fold.train_index)]
        test = frame.loc[list(fold.test_index)]
        assert set(train["accession_number"]).isdisjoint(
            set(test["accession_number"])
        )
        assert train["target_trading_day"].max() < fold.first_test_anchor_day


def test_moving_block_bootstrap_is_deterministic():
    frame = _synthetic_frame(80)
    frame["base"] = 0.0
    frame["cand"] = frame["return_pct"] * 0.1
    a = moving_block_bootstrap(
        frame,
        baseline_col="base",
        candidate_col="cand",
        block_length=10,
        reps=100,
        seed=123,
    )
    b = moving_block_bootstrap(
        frame,
        baseline_col="base",
        candidate_col="cand",
        block_length=10,
        reps=100,
        seed=123,
    )
    assert a["mae_delta_ci95"] == b["mae_delta_ci95"]
    assert a["mae_delta_baseline_minus_candidate_pct"] > 0


def test_simple_pipelines_build_for_all_families():
    numeric = ["x1", "x2"]
    categorical = ["cat"]
    for family in SIMPLE_FAMILIES:
        pipe = _simple_pipeline(numeric, categorical, family)
        assert pipe.named_steps["model"] is not None


def test_robustness_package_does_not_write_database():
    source = Path(
        "pipeline/event_brain_robustness_v0021.py"
    ).read_text(encoding="utf-8")
    assert "INSERT INTO" not in source
    assert "UPDATE " not in source
    assert "DELETE FROM" not in source


def test_no_new_feature_contract_is_preserved():
    source = Path(
        "models/events/robustness_v0021.py"
    ).read_text(encoding="utf-8")
    assert "MARKET_FEATURES + EVENT_FEATURES_NUMERIC" in source
    assert "MARKET_CATEGORICAL + EVENT_FEATURES_CATEGORICAL" in source
    # The package imports feature lists instead of inventing a V0021 feature set.
    assert "NEW_EVENT_FEATURE" not in source

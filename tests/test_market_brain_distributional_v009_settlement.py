from __future__ import annotations

import sqlite3
from pathlib import Path

from evaluation.market.distributional_v009 import settle_available_outcomes
from models.market.distributional_v009_prospective import load_config
from storage.prospective_registry import initialize_registry, seal_prediction_batch
from tests.test_market_brain_distributional_v009 import (
    _prediction,
    _register_minimal_experiment_and_fit,
)


def test_settlement_links_one_outcome_to_both_frozen_distributions(tmp_path):
    registry = tmp_path / "registry.db"
    core = tmp_path / "core.db"
    initialize_registry(registry)
    _register_minimal_experiment_and_fit(registry)
    batch = {
        "batch_id": "batch",
        "experiment_version": "exp",
        "fit_id": "fit",
        "origin_trading_day": "2026-08-28",
        "state_time": "2026-08-28T20:00:00+00:00",
        "sealed_at_utc": "2026-08-28T20:05:00+00:00",
        "seal_delay_seconds": 300.0,
        "eligible_assets": 1,
        "predicted_assets": 1,
        "state_snapshot_sha256": "state-hash",
        "status": "SEALED",
        "metadata_json": "{}",
    }
    seal_prediction_batch(
        registry,
        batch,
        [_prediction("candidate"), _prediction("reference")],
    )
    with sqlite3.connect(core) as conn:
        conn.execute(
            """
            CREATE TABLE market_daily_v003_labels(
              label_id TEXT,state_id TEXT,asset_id INTEGER,
              origin_trading_day TEXT,target_trading_day TEXT,
              horizon_sessions INTEGER,return_pct REAL,mfe_pct REAL,
              mae_pct REAL,realized_path_vol_pct REAL,
              corporate_action_overlap INTEGER,label_status TEXT,
              label_version TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO market_daily_v003_labels VALUES(
              'label','state',1,'2026-08-28','2026-08-31',1,
              1.5,2.0,-0.5,0.7,0,'usable','label-v'
            )
            """
        )
        conn.commit()

    cfg = load_config(Path("config/market_brain_distributional_v009.json"))
    cfg["version"] = "exp"
    cfg["label_version"] = "label-v"
    result = settle_available_outcomes(
        registry,
        core,
        cfg,
        observed_at_utc="2026-08-31T20:01:00+00:00",
    )
    assert result["outcomes_linked"] == 1
    assert result["scores_linked"] == 2

    with sqlite3.connect(registry) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM prospective_prediction_outcomes"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM prospective_distribution_scores"
        ).fetchone()[0] == 2

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from database.apply_migration_021 import apply
from database.apply_migration_022 import apply as apply_evaluation_immutability
from evaluation.market.distributional_v009 import score_prediction
from models.market.distributional_v009_prospective import (
    load_config,
    load_prediction_frame,
)
from storage.prospective_registry import (
    canonical_json,
    initialize_registry,
    register_experiment,
    register_fit,
    seal_prediction_batch,
    sha256_json,
)


def test_v009_config_freezes_prospective_confirmation():
    cfg = load_config(Path("config/market_brain_distributional_v009.json"))
    assert cfg["horizon_sessions"] == 1
    assert cfg["fit_policy"] == "single_pre_holdout_fit"
    assert cfg["refit_during_confirmatory_window"] is False
    assert cfg["confirmatory_origin_days"] == 252
    assert cfg["preliminary_descriptive_origin_days"] == 126
    assert cfg["maximum_seal_delay_hours_from_state_time"] == 16
    assert cfg["not_before_origin_day"] == "2026-08-28"
    assert cfg["no_retroactive_prediction_backfill"] is True
    assert cfg["primary_reference"] == "vol63_raw_static"
    assert len(cfg["frozen_own_features"]) == 14


def test_migration_021_is_idempotent_and_predictions_are_immutable(tmp_path):
    db = tmp_path / "registry.db"
    assert apply(db) == "applied"
    assert apply(db) == "already_applied"
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT name FROM schema_migrations WHERE version='021'"
        ).fetchone()[0] == "prospective_prediction_registry"
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
    assert {
        "prospective_experiments",
        "prospective_model_fits",
        "prospective_prediction_batches",
        "prospective_distribution_predictions",
        "prospective_prediction_outcomes",
        "prospective_distribution_scores",
        "prospective_evaluation_runs",
    } <= tables


def test_migration_022_is_additive_and_evaluations_are_immutable(tmp_path):
    db = tmp_path / "registry.db"
    apply(db)
    assert apply_evaluation_immutability(db) == "applied"
    assert apply_evaluation_immutability(db) == "already_applied"
    register_experiment(
        db,
        experiment_version="exp",
        registry_version="registry",
        config_sha256="c",
        plan={"status": "PASS"},
        source_checkpoint_sha256="s",
        registered_at_utc="2026-08-27T12:00:00+00:00",
    )
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO prospective_evaluation_runs VALUES(
              'evaluation','exp','evaluation-v1',
              '2026-08-27T12:05:00+00:00','first-252',NULL,NULL,
              0,0,'WAITING','hash','{}'
            )
            """
        )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "UPDATE prospective_evaluation_runs SET status='CHANGED'"
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute("DELETE FROM prospective_evaluation_runs")


def _register_minimal_experiment_and_fit(db: Path) -> None:
    register_experiment(
        db,
        experiment_version="exp",
        registry_version="registry",
        config_sha256="c",
        plan={"status": "PASS"},
        source_checkpoint_sha256="s",
        registered_at_utc="2026-08-27T12:00:00+00:00",
    )
    register_fit(
        db,
        {
            "fit_id": "fit",
            "experiment_version": "exp",
            "model_version": "model",
            "fitted_at_utc": "2026-08-27T12:01:00+00:00",
            "horizon_sessions": 1,
            "training_first_origin_day": "2020-01-01",
            "training_last_origin_day": "2026-08-26",
            "training_last_target_day": "2026-08-27",
            "training_rows": 1000,
            "training_origin_days": 500,
            "training_assets": 2,
            "training_data_sha256": "train",
            "feature_manifest_sha256": "manifest",
            "algorithm_contract_sha256": "algorithm",
            "artifact_path": "/tmp/model.joblib",
            "artifact_sha256": "artifact",
            "metadata_json": "{}",
        },
    )


def _prediction(role: str) -> dict:
    payload = {
        "prediction_id": f"prediction-{role}",
        "batch_id": "batch",
        "model_role": role,
        "model_version": f"model-{role}",
        "asset_id": 1,
        "ticker": "AAA",
        "state_id": "state",
        "origin_trading_day": "2026-08-28",
        "state_time": "2026-08-28T20:00:00+00:00",
        "state_point_in_time_verified": 0,
        "q05": -2.0,
        "q25": -1.0,
        "q50": 0.0,
        "q75": 1.0,
        "q95": 2.0,
        "probability_positive": 0.5,
        "feature_snapshot_json": "{}",
        "feature_snapshot_sha256": "feature",
    }
    payload["payload_sha256"] = sha256_json(payload)
    return payload


def test_batch_seal_is_idempotent_and_sql_triggers_block_mutation(tmp_path):
    db = tmp_path / "registry.db"
    initialize_registry(db)
    _register_minimal_experiment_and_fit(db)
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
    predictions = [_prediction("candidate"), _prediction("reference")]
    assert seal_prediction_batch(db, batch, predictions) == "sealed"
    assert seal_prediction_batch(db, batch, predictions) == "already_sealed"

    with sqlite3.connect(db) as conn:
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                """
                UPDATE prospective_distribution_predictions
                SET q50=1 WHERE prediction_id='prediction-candidate'
                """
            )
        with pytest.raises(sqlite3.IntegrityError, match="immutable"):
            conn.execute(
                "DELETE FROM prospective_prediction_batches WHERE batch_id='batch'"
            )


def test_same_origin_cannot_be_resealed_with_different_payload(tmp_path):
    db = tmp_path / "registry.db"
    initialize_registry(db)
    _register_minimal_experiment_and_fit(db)
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
    predictions = [_prediction("candidate"), _prediction("reference")]
    seal_prediction_batch(db, batch, predictions)
    changed = dict(batch)
    changed["sealed_at_utc"] = "2026-08-28T20:06:00+00:00"
    with pytest.raises(RuntimeError, match="different immutable batch"):
        seal_prediction_batch(db, changed, predictions)


def _minimal_core(path: Path, cfg: dict, origin_day: str) -> None:
    features = list(cfg["frozen_own_features"])
    feature_sql = ",\n".join(f"{name} REAL" for name in features)
    with sqlite3.connect(path) as conn:
        conn.execute(
            f"""
            CREATE TABLE market_daily_v003_states(
              state_id TEXT,asset_id INTEGER,ticker TEXT,sector TEXT,
              trading_day TEXT,state_time TEXT,feature_version TEXT,
              state_point_in_time_verified INTEGER,{feature_sql}
            )
            """
        )
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
        columns = [
            "state_id",
            "asset_id",
            "ticker",
            "sector",
            "trading_day",
            "state_time",
            "feature_version",
            "state_point_in_time_verified",
            *features,
        ]
        placeholders = ",".join("?" for _ in columns)
        for asset_id in (1, 2):
            values = [
                f"state-{asset_id}",
                asset_id,
                f"A{asset_id}",
                "S",
                origin_day,
                f"{origin_day}T20:00:00+00:00",
                cfg["market_feature_version"],
                0,
                *[1.0 for _ in features],
            ]
            conn.execute(
                f"""
                INSERT INTO market_daily_v003_states(
                  {",".join(columns)}
                ) VALUES ({placeholders})
                """,
                values,
            )
        conn.commit()


def test_prediction_loader_rejects_retroactive_origin_before_db_access(tmp_path):
    cfg = load_config(Path("config/market_brain_distributional_v009.json"))
    with pytest.raises(RuntimeError, match="retroactive"):
        load_prediction_frame(
            tmp_path / "missing.db",
            cfg,
            [1, 2],
            "2026-08-27",
        )


def test_prediction_loader_rejects_origin_with_observed_outcome(tmp_path):
    cfg = load_config(Path("config/market_brain_distributional_v009.json"))
    cfg["minimum_predictions_per_origin"] = 2
    core = tmp_path / "core.db"
    _minimal_core(core, cfg, "2026-08-28")
    with sqlite3.connect(core) as conn:
        conn.execute(
            """
            INSERT INTO market_daily_v003_labels VALUES(
              'label','state-1',1,'2026-08-28','2026-08-31',1,
              1.0,1.0,0.0,0.0,0,'usable',?
            )
            """,
            (cfg["label_version"],),
        )
        conn.commit()
    with pytest.raises(RuntimeError, match="already observed"):
        load_prediction_frame(core, cfg, [1, 2], "2026-08-28")


def test_score_prediction_uses_proper_losses():
    prediction = _prediction("candidate")
    score = score_prediction(
        prediction,
        "outcome",
        actual=1.5,
        scored_at_utc="2026-08-31T20:01:00+00:00",
    )
    assert score["median_absolute_error"] == 1.5
    assert score["brier_positive"] == 0.25
    assert score["pinball_q50"] == 0.75
    assert score["mean_pinball_loss"] > 0
    assert score["payload_sha256"] == sha256_json(
        {key: value for key, value in score.items() if key != "payload_sha256"}
    )

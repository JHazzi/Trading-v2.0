from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Iterable, Mapping

from database.apply_migration_021 import apply as apply_migration_021
from database.apply_migration_022 import apply as apply_migration_022


def canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def sha256_json(payload: Any) -> str:
    return sha256_text(canonical_json(payload))


def stable_id(prefix: str, payload: Any) -> str:
    return f"{prefix}_{sha256_json(payload)[:32]}"


def connect_registry(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys=ON")
    return conn


def initialize_registry(path: Path) -> dict[str, str]:
    return {
        "021_prospective_prediction_registry": apply_migration_021(path),
        "022_prospective_evaluation_immutability": apply_migration_022(path),
    }


def register_experiment(
    path: Path,
    *,
    experiment_version: str,
    registry_version: str,
    config_sha256: str,
    plan: Mapping[str, Any],
    source_checkpoint_sha256: str,
    registered_at_utc: str,
) -> str:
    plan_json = canonical_json(plan)
    values = {
        "experiment_version": experiment_version,
        "registry_version": registry_version,
        "config_sha256": config_sha256,
        "plan_sha256": sha256_text(plan_json),
        "source_checkpoint_sha256": source_checkpoint_sha256,
        "registered_at_utc": registered_at_utc,
        "status": "PREREGISTERED",
        "plan_json": plan_json,
    }
    with connect_registry(path) as conn:
        row = conn.execute(
            """
            SELECT * FROM prospective_experiments
            WHERE experiment_version=?
            """,
            (experiment_version,),
        ).fetchone()
        if row is not None:
            stored = dict(row)
            if any(stored[key] != value for key, value in values.items() if key != "registered_at_utc"):
                raise RuntimeError(
                    "prospective experiment already registered with a "
                    "different immutable contract"
                )
            return "already_registered"
        conn.execute(
            """
            INSERT INTO prospective_experiments(
              experiment_version,registry_version,config_sha256,plan_sha256,
              source_checkpoint_sha256,registered_at_utc,status,plan_json
            ) VALUES (
              :experiment_version,:registry_version,:config_sha256,:plan_sha256,
              :source_checkpoint_sha256,:registered_at_utc,:status,:plan_json
            )
            """,
            values,
        )
        conn.commit()
    return "registered"


def register_fit(path: Path, fit: Mapping[str, Any]) -> str:
    keys = (
        "fit_id",
        "experiment_version",
        "model_version",
        "fitted_at_utc",
        "horizon_sessions",
        "training_first_origin_day",
        "training_last_origin_day",
        "training_last_target_day",
        "training_rows",
        "training_origin_days",
        "training_assets",
        "training_data_sha256",
        "feature_manifest_sha256",
        "algorithm_contract_sha256",
        "artifact_path",
        "artifact_sha256",
        "metadata_json",
    )
    values = {key: fit[key] for key in keys}
    with connect_registry(path) as conn:
        row = conn.execute(
            "SELECT * FROM prospective_model_fits WHERE experiment_version=?",
            (fit["experiment_version"],),
        ).fetchone()
        if row is not None:
            stored = dict(row)
            if any(stored[key] != value for key, value in values.items()):
                raise RuntimeError(
                    "the single pre-holdout fit is already frozen; refitting "
                    "or replacing its artifact is forbidden"
                )
            return "already_registered"
        conn.execute(
            """
            INSERT INTO prospective_model_fits(
              fit_id,experiment_version,model_version,fitted_at_utc,
              horizon_sessions,training_first_origin_day,
              training_last_origin_day,training_last_target_day,
              training_rows,training_origin_days,training_assets,
              training_data_sha256,feature_manifest_sha256,
              algorithm_contract_sha256,artifact_path,artifact_sha256,
              metadata_json
            ) VALUES (
              :fit_id,:experiment_version,:model_version,:fitted_at_utc,
              :horizon_sessions,:training_first_origin_day,
              :training_last_origin_day,:training_last_target_day,
              :training_rows,:training_origin_days,:training_assets,
              :training_data_sha256,:feature_manifest_sha256,
              :algorithm_contract_sha256,:artifact_path,:artifact_sha256,
              :metadata_json
            )
            """,
            values,
        )
        conn.commit()
    return "registered"


def _prediction_hashes(
    conn: sqlite3.Connection,
    batch_id: str,
) -> list[tuple[str, str]]:
    return [
        (str(row["prediction_id"]), str(row["payload_sha256"]))
        for row in conn.execute(
            """
            SELECT prediction_id,payload_sha256
            FROM prospective_distribution_predictions
            WHERE batch_id=?
            ORDER BY prediction_id
            """,
            (batch_id,),
        )
    ]


def seal_prediction_batch(
    path: Path,
    batch: Mapping[str, Any],
    predictions: Iterable[Mapping[str, Any]],
) -> str:
    records = list(predictions)
    expected_hashes = sorted(
        (str(row["prediction_id"]), str(row["payload_sha256"]))
        for row in records
    )
    if len(expected_hashes) != len(set(x[0] for x in expected_hashes)):
        raise ValueError("duplicate prediction IDs in seal payload")

    batch_keys = (
        "batch_id",
        "experiment_version",
        "fit_id",
        "origin_trading_day",
        "state_time",
        "sealed_at_utc",
        "seal_delay_seconds",
        "eligible_assets",
        "predicted_assets",
        "state_snapshot_sha256",
        "status",
        "metadata_json",
    )
    batch_values = {key: batch[key] for key in batch_keys}
    prediction_keys = (
        "prediction_id",
        "batch_id",
        "model_role",
        "model_version",
        "asset_id",
        "ticker",
        "state_id",
        "origin_trading_day",
        "state_time",
        "state_point_in_time_verified",
        "q05",
        "q25",
        "q50",
        "q75",
        "q95",
        "probability_positive",
        "feature_snapshot_json",
        "feature_snapshot_sha256",
        "payload_sha256",
    )

    conn = connect_registry(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        existing = conn.execute(
            """
            SELECT * FROM prospective_prediction_batches
            WHERE experiment_version=? AND origin_trading_day=?
            """,
            (
                batch["experiment_version"],
                batch["origin_trading_day"],
            ),
        ).fetchone()
        if existing is not None:
            stored = dict(existing)
            if any(stored[key] != value for key, value in batch_values.items()):
                raise RuntimeError(
                    "origin day already sealed with a different immutable batch"
                )
            if _prediction_hashes(conn, str(batch["batch_id"])) != expected_hashes:
                raise RuntimeError(
                    "origin day batch exists but prediction payload differs"
                )
            conn.rollback()
            return "already_sealed"

        conn.execute(
            """
            INSERT INTO prospective_prediction_batches(
              batch_id,experiment_version,fit_id,origin_trading_day,
              state_time,sealed_at_utc,seal_delay_seconds,eligible_assets,
              predicted_assets,state_snapshot_sha256,status,metadata_json
            ) VALUES (
              :batch_id,:experiment_version,:fit_id,:origin_trading_day,
              :state_time,:sealed_at_utc,:seal_delay_seconds,:eligible_assets,
              :predicted_assets,:state_snapshot_sha256,:status,:metadata_json
            )
            """,
            batch_values,
        )
        conn.executemany(
            """
            INSERT INTO prospective_distribution_predictions(
              prediction_id,batch_id,model_role,model_version,asset_id,ticker,
              state_id,origin_trading_day,state_time,
              state_point_in_time_verified,q05,q25,q50,q75,q95,
              probability_positive,feature_snapshot_json,
              feature_snapshot_sha256,payload_sha256
            ) VALUES (
              :prediction_id,:batch_id,:model_role,:model_version,:asset_id,
              :ticker,:state_id,:origin_trading_day,:state_time,
              :state_point_in_time_verified,:q05,:q25,:q50,:q75,:q95,
              :probability_positive,:feature_snapshot_json,
              :feature_snapshot_sha256,:payload_sha256
            )
            """,
            [{key: row[key] for key in prediction_keys} for row in records],
        )
        conn.commit()
        return "sealed"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def insert_outcome_and_scores(
    path: Path,
    outcome: Mapping[str, Any],
    scores: Iterable[Mapping[str, Any]],
) -> str:
    score_records = list(scores)
    outcome_keys = (
        "outcome_id",
        "batch_id",
        "asset_id",
        "origin_trading_day",
        "target_trading_day",
        "horizon_sessions",
        "label_version",
        "label_status",
        "corporate_action_overlap",
        "return_pct",
        "mfe_pct",
        "mae_pct",
        "realized_path_vol_pct",
        "observed_at_utc",
        "source_label_id",
        "payload_sha256",
    )
    score_keys = (
        "prediction_id",
        "outcome_id",
        "mean_pinball_loss",
        "pinball_q05",
        "pinball_q25",
        "pinball_q50",
        "pinball_q75",
        "pinball_q95",
        "median_absolute_error",
        "brier_positive",
        "hit_q05",
        "hit_q25",
        "hit_q50",
        "hit_q75",
        "hit_q95",
        "scored_at_utc",
        "payload_sha256",
    )
    values = {key: outcome[key] for key in outcome_keys}
    conn = connect_registry(path)
    try:
        conn.execute("BEGIN IMMEDIATE")
        row = conn.execute(
            """
            SELECT * FROM prospective_prediction_outcomes
            WHERE batch_id=? AND asset_id=?
            """,
            (outcome["batch_id"], int(outcome["asset_id"])),
        ).fetchone()
        if row is not None:
            stored = dict(row)
            if any(stored[key] != value for key, value in values.items()):
                raise RuntimeError(
                    "outcome already linked with a different immutable payload"
                )
            stored_scores = {
                str(x["prediction_id"]): str(x["payload_sha256"])
                for x in conn.execute(
                    """
                    SELECT prediction_id,payload_sha256
                    FROM prospective_distribution_scores
                    WHERE outcome_id=?
                    """,
                    (outcome["outcome_id"],),
                )
            }
            expected_scores = {
                str(x["prediction_id"]): str(x["payload_sha256"])
                for x in score_records
            }
            if stored_scores != expected_scores:
                raise RuntimeError("stored score payload differs")
            conn.rollback()
            return "already_linked"

        conn.execute(
            """
            INSERT INTO prospective_prediction_outcomes(
              outcome_id,batch_id,asset_id,origin_trading_day,
              target_trading_day,horizon_sessions,label_version,label_status,
              corporate_action_overlap,return_pct,mfe_pct,mae_pct,
              realized_path_vol_pct,observed_at_utc,source_label_id,
              payload_sha256
            ) VALUES (
              :outcome_id,:batch_id,:asset_id,:origin_trading_day,
              :target_trading_day,:horizon_sessions,:label_version,
              :label_status,:corporate_action_overlap,:return_pct,:mfe_pct,
              :mae_pct,:realized_path_vol_pct,:observed_at_utc,
              :source_label_id,:payload_sha256
            )
            """,
            values,
        )
        if score_records:
            conn.executemany(
                """
                INSERT INTO prospective_distribution_scores(
                  prediction_id,outcome_id,mean_pinball_loss,pinball_q05,
                  pinball_q25,pinball_q50,pinball_q75,pinball_q95,
                  median_absolute_error,brier_positive,hit_q05,hit_q25,
                  hit_q50,hit_q75,hit_q95,scored_at_utc,payload_sha256
                ) VALUES (
                  :prediction_id,:outcome_id,:mean_pinball_loss,:pinball_q05,
                  :pinball_q25,:pinball_q50,:pinball_q75,:pinball_q95,
                  :median_absolute_error,:brier_positive,:hit_q05,:hit_q25,
                  :hit_q50,:hit_q75,:hit_q95,:scored_at_utc,:payload_sha256
                )
                """,
                [{key: row[key] for key in score_keys} for row in score_records],
            )
        conn.commit()
        return "linked"
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()


def append_evaluation(
    path: Path,
    row: Mapping[str, Any],
) -> str:
    keys = (
        "evaluation_id",
        "experiment_version",
        "evaluation_version",
        "evaluated_at_utc",
        "cohort_policy",
        "first_origin_day",
        "last_origin_day",
        "origin_days",
        "usable_rows",
        "status",
        "report_sha256",
        "report_json",
    )
    values = {key: row[key] for key in keys}
    with connect_registry(path) as conn:
        existing = conn.execute(
            """
            SELECT report_sha256 FROM prospective_evaluation_runs
            WHERE evaluation_id=?
            """,
            (row["evaluation_id"],),
        ).fetchone()
        if existing is not None:
            if str(existing[0]) != str(row["report_sha256"]):
                raise RuntimeError("evaluation ID collision")
            return "already_registered"
        conn.execute(
            """
            INSERT INTO prospective_evaluation_runs(
              evaluation_id,experiment_version,evaluation_version,
              evaluated_at_utc,cohort_policy,first_origin_day,last_origin_day,
              origin_days,usable_rows,status,report_sha256,report_json
            ) VALUES (
              :evaluation_id,:experiment_version,:evaluation_version,
              :evaluated_at_utc,:cohort_policy,:first_origin_day,
              :last_origin_day,:origin_days,:usable_rows,:status,
              :report_sha256,:report_json
            )
            """,
            values,
        )
        conn.commit()
    return "registered"

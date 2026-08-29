"""Frozen, plan-only preregistration for Distributional Event Brain V001.

This module may inspect feature schemas, causal labels' availability/status and
temporal/group support.  It never reads outcome values into a plan artifact,
fits a model, imports a V009 artifact, or offers a benchmark stage.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import shutil
import sqlite3
import tempfile
from typing import Any, Iterable, Mapping


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "distributional_event_brain_v001.json"
EXPECTED_VERSION = "distributional_event_brain_preregistration_v001"
EXPECTED_BENCHMARK = "distributional_event_brain_v001"
EXPECTED_MODEL = "distributional_event_quantile_offset_head_v001"
EXPECTED_FOLDS = (
    (0, "2020-05-06", "2021-08-06"),
    (1, "2021-08-09", "2022-11-08"),
    (2, "2022-11-09", "2024-02-13"),
    (3, "2024-02-14", "2025-05-19"),
    (4, "2025-05-20", "2026-08-21"),
)
OUTCOME_VALUE_COLUMNS = frozenset(
    {"return_pct", "mfe_pct", "mae_pct", "realized_path_vol_pct", "trace_json"}
)


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def rooted(value: str | Path) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def ro_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=15)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    conn.execute("BEGIN")
    return conn


def _require(condition: bool, message: str) -> None:
    if not condition:
        raise ValueError(message)


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    _require(cfg.get("version") == EXPECTED_VERSION, "unexpected preregistration version")
    _require(cfg.get("benchmark_version") == EXPECTED_BENCHMARK, "unexpected benchmark version")
    _require(cfg.get("model_version") == EXPECTED_MODEL, "unexpected model version")
    _require(cfg.get("stage_scope") == "plan_only_no_training", "training scope enabled")

    dataset = cfg["dataset"]
    _require(dataset["contract"] == "distributional_event_close_aligned_v002",
             "V002 dataset contract required")
    _require(dataset["event_feature_version"] == "event_state_v0031_deep",
             "event feature version changed")
    _require(dataset["market_feature_version"] == "market_daily_state_v003_core",
             "market feature version changed")
    _require(dataset["label_version"] == "event_distributional_close_aligned_v002",
             "event label version changed")
    _require(dataset["strict_historical_pit"] is False, "historical corpus is not strict PIT")

    scenario = cfg["scenario_contract"]
    _require(scenario["primary_delay_seconds"] == 0, "zero-delay primary changed")
    _require(scenario["diagnostic_delay_seconds"] == [3600, 86400],
             "delay sensitivities changed")
    _require(scenario["diagnostic_delays_cannot_rescue_primary"] is True,
             "delay rescue guard disabled")
    _require(scenario["scenarios_are_not_independent_observations"] is True,
             "delay scenarios treated as independent")

    target = cfg["target_contract"]
    _require(target["primary_target"] == "return_pct", "primary target changed")
    _require(target["primary_horizon_sessions"] == 1, "H1 primary changed")
    _require(target["diagnostic_horizons_sessions"] == [3, 5],
             "diagnostic horizons changed")
    _require(target["selection_sensitivity_horizons_sessions"] == [10],
             "H10 selection sensitivity changed")
    _require(OUTCOME_VALUE_COLUMNS.issubset(target["not_model_visible"]),
             "outcome leakage denylist incomplete")
    _require(tuple(float(q) for q in cfg["quantiles"]) == (0.05, 0.25, 0.5, 0.75, 0.95),
             "quantile contract changed")

    market = cfg["market_brain"]
    _require(market["source_version"] ==
             "market_brain_distributional_v0081_endogenous_closure_v001",
             "historical Market Brain source changed")
    _require(market["source_model_version"] ==
             "market_brain_distributional_v0081_hgb_own_state_raw_v001",
             "historical Market Brain model changed")
    _require(market["refit_policy"] ==
             "fresh_historical_fit_inside_each_outer_fold_never_final_v009_fit",
             "Market Brain refit isolation changed")
    _require(market["event_head_training_prediction_policy"] ==
             "three_block_expanding_inner_oof_market_predictions",
             "OOF residual policy changed")
    _require(market["post_model_quantile_calibration"] == "none",
             "post-model calibration is forbidden")
    _require(len(market["frozen_own_features"]) == 14 and
             len(set(market["frozen_own_features"])) == 14,
             "frozen Market Brain feature family changed")
    _require(int(market["minimum_inner_market_train_origin_days"]) == 378,
             "inner Market Brain history gate changed")
    _require(int(market["inner_oof_blocks"]) == 3, "inner OOF block count changed")

    features = cfg["event_features"]
    _require(len(features["numeric"]) == len(set(features["numeric"])) == 17,
             "event numeric feature family changed")
    _require(len(features["categorical_vocabulary"]) ==
             len(set(features["categorical_vocabulary"])) == 18,
             "event taxonomy vocabulary changed")
    _require(features["economic_signs_or_importance_hardcoded"] is False,
             "predictive economic semantics hardcoded")

    head = cfg["event_head"]
    _require(head["estimator"] == "sklearn.linear_model.QuantileRegressor",
             "event-head estimator changed")
    _require(head["alpha_l1"] == 0.1 and head["hyperparameter_selection"] == "none",
             "event-head regularization selection enabled")
    _require(head["probability_or_direction_head"] == "none",
             "direction head is outside V001")
    _require(all(head[key] is False for key in (
        "graph_features", "text_embeddings", "expectation_or_surprise_features"
    )), "deferred event information enabled")

    folds = tuple(
        (int(f["fold_id"]), f["first_test_day"], f["last_test_day"])
        for f in cfg["outer_folds"]
    )
    _require(folds == EXPECTED_FOLDS, "outer folds differ from frozen V008.1 H1 windows")
    _require(cfg["purge_contract"]["group_kinds"] == ["event", "filing", "content"],
             "group purge family changed")
    _require(cfg["controls"]["capacity_placebo"]["seeds"] == [11, 29, 47, 71, 101],
             "placebo seeds changed")

    guards = cfg["guards"]
    false_guards = (
        "training_authorized_by_this_stage", "benchmark_stage_present_in_this_module",
        "v009_artifacts_loaded_or_modified", "v009_fit_used_historically",
        "random_split_allowed", "posthoc_horizon_rescue_allowed",
        "posthoc_feature_change_allowed", "additional_sec_ingestion_allowed",
        "outcome_values_allowed_in_plan_artifacts",
    )
    _require(all(guards[key] is False for key in false_guards),
             "one or more scientific guards were disabled")
    _require(guards["fresh_untouched_holdout_required_for_any_promotion"] is True,
             "historical experiment cannot promote without fresh holdout")
    return cfg


def _json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_paths(cfg: Mapping[str, Any]) -> dict[str, Path]:
    dataset, market = cfg["dataset"], cfg["market_brain"]
    return {
        "dataset_database": rooted(dataset["database"]),
        "dataset_manifest": rooted(dataset["manifest"]),
        "dataset_audit": rooted(dataset["audit"]),
        "market_config": rooted(market["source_config"]),
        "market_h1_report": rooted(market["source_h1_report"]),
        "market_feature_manifest": rooted(market["source_feature_manifest"]),
        "market_core_database": rooted(market["core_database"]),
    }


def validate_sources(cfg: Mapping[str, Any]) -> dict[str, Any]:
    paths = _source_paths(cfg)
    missing = [name for name, path in paths.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(f"missing preregistration sources: {missing}")

    dataset, market = cfg["dataset"], cfg["market_brain"]
    expected_hashes = {
        "dataset_database": dataset["database_sha256"],
        "dataset_audit": dataset["audit_sha256"],
        "market_config": market["source_config_sha256"],
        "market_h1_report": market["source_h1_report_sha256"],
        "market_feature_manifest": market["source_feature_manifest_sha256"],
    }
    observed_hashes = {name: file_digest(paths[name]) for name in expected_hashes}
    drift = {
        name: {"expected": expected_hashes[name], "observed": observed_hashes[name]}
        for name in expected_hashes if expected_hashes[name] != observed_hashes[name]
    }
    if drift:
        raise RuntimeError(f"frozen source hash drift: {drift}")

    dataset_manifest = _json(paths["dataset_manifest"])
    dataset_audit = _json(paths["dataset_audit"])
    market_config = _json(paths["market_config"])
    h1_report = _json(paths["market_h1_report"])
    feature_manifest = _json(paths["market_feature_manifest"])

    _require(dataset_manifest["dataset_sha256"] == dataset["database_sha256"],
             "dataset sidecar hash mismatch")
    _require(dataset_manifest["contract_sha256"] == dataset["contract_sha256"],
             "dataset contract hash mismatch")
    _require(dataset_manifest["contract"]["dataset_contract"] == dataset["contract"],
             "dataset identity mismatch")
    _require(dataset_manifest["training_status"].startswith("BLOCKED_"),
             "V002 training guard unexpectedly absent")
    _require(dataset_audit["integrity_status"] == "PASS" and not dataset_audit["failures"],
             "V002 persisted/replay integrity failed")
    _require(dataset_audit["source_states"] == dataset_audit["examined_states"] == 2001,
             "V002 full-run coverage changed")
    _require(dataset_audit["strict_pit"] is False, "V002 was relabeled strict PIT")

    _require(market_config["version"] == market["source_version"],
             "V008.1 source version mismatch")
    _require(market_config["model_version"] == market["source_model_version"],
             "V008.1 model version mismatch")
    _require(market_config["frozen_own_features"] == market["frozen_own_features"],
             "Market Brain feature manifest drift")
    _require(market_config["fixed_model_profile"] == market["fixed_model_profile"],
             "Market Brain model profile drift")
    _require(h1_report["horizon_gate"]["status"] ==
             "PASS_DEVELOPMENTAL_REQUIRES_FRESH_HOLDOUT",
             "V008.1 developmental source gate changed")
    _require(h1_report["primary_candidate"] == "hgb_own_state_raw" and
             h1_report["primary_reference"] == "vol63_raw",
             "V008.1 source comparison changed")
    source_folds = tuple(
        (int(f["fold_id"]), f["first_test_day"], f["last_test_day"])
        for f in h1_report["fold_contract"]
    )
    _require(source_folds == EXPECTED_FOLDS, "V008.1 persisted fold contract changed")
    _require(feature_manifest["own_state"] == market["frozen_own_features"],
             "V008.1 feature manifest changed")
    return {
        "status": "PASS",
        "file_sha256": observed_hashes,
        "dataset_manifest_sha256": file_digest(paths["dataset_manifest"]),
        "dataset_contract_sha256": dataset_manifest["contract_sha256"],
        "dataset_integrity_status": dataset_audit["integrity_status"],
        "dataset_scientific_status": dataset_audit["status"],
        "v0081_h1_gate": h1_report["horizon_gate"]["status"],
        "v009_files_opened": 0,
    }

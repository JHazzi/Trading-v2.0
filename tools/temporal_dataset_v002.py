"""Materialize horizon-conditioned total-shareholder-return outcomes.

V002 is additive to Market Temporal V001.  It reuses V001's exact, parity-
verified origin/session grid, reconstructs cash-inclusive economic returns from
explicit corporate-action observations, and keeps provider Adjusted Close in a
strictly audit-only role.  Source, Core and V001 databases are always read-only;
no model is trained and V009 is outside this program's scope.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterable, Sequence

if __package__:
    from .temporal_dataset_v001 import (
        BuildBlocked,
        canonical,
        digest,
        file_digest,
        file_state,
        parse_tau_spec,
        resolve_taus,
        ro_connect,
    )
else:  # pragma: no cover - exercised by the documented script entry point
    from temporal_dataset_v001 import (
        BuildBlocked,
        canonical,
        digest,
        file_digest,
        file_state,
        parse_tau_spec,
        resolve_taus,
        ro_connect,
    )


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "database" / "market_data_v2.db"
DEFAULT_CORE = ROOT / "data" / "processed" / "market_daily_v003_core.db"
DEFAULT_V001 = ROOT / "data" / "processed" / "market_temporal_v001.db"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "market_temporal_v002.db"
DEFAULT_CONFIG = ROOT / "config" / "temporal_dataset_v002.json"
DEFAULT_REPORT_DIR = ROOT / "reports" / "market_temporal_v002"
QUALITY_VIEW = "daily_price_quality_gated_observations_v002"
CORE_PARITY_HORIZONS = (1, 3, 5, 10)
SUPPORTED_ACTION_TYPES = {"dividend", "capital_gain", "stock_split"}
CASH_ACTION_TYPES = {"dividend", "capital_gain"}


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
CREATE TABLE dataset_horizons(
 tau_sessions INTEGER PRIMARY KEY CHECK(tau_sessions BETWEEN 1 AND 252),
 materialized INTEGER NOT NULL CHECK(materialized IN (0,1)),
 role_json TEXT NOT NULL
);
CREATE TABLE temporal_price_points(
 asset_id INTEGER NOT NULL,asset_session_index INTEGER NOT NULL,
 trading_day TEXT NOT NULL,bar_end_utc TEXT NOT NULL,provider_close REAL NOT NULL,
 observed_adjusted_close REAL,price_observation_id TEXT NOT NULL,
 observation_sequence INTEGER NOT NULL,observed_at TEXT NOT NULL,
 causal_available_at TEXT NOT NULL,
 PRIMARY KEY(asset_id,asset_session_index),UNIQUE(asset_id,trading_day)
) WITHOUT ROWID;
CREATE TABLE temporal_origins(
 origin_id INTEGER PRIMARY KEY,state_id TEXT NOT NULL UNIQUE,
 asset_id INTEGER NOT NULL,ticker TEXT NOT NULL,sector TEXT NOT NULL,
 origin_trading_day TEXT NOT NULL,state_time TEXT NOT NULL,
 origin_session_index INTEGER NOT NULL,provider_close_origin REAL NOT NULL,
 market_feature_version TEXT NOT NULL,UNIQUE(asset_id,origin_trading_day),
 FOREIGN KEY(asset_id,origin_session_index)
   REFERENCES temporal_price_points(asset_id,asset_session_index)
);
CREATE TABLE temporal_corporate_actions(
 asset_id INTEGER NOT NULL,effective_trading_day TEXT NOT NULL,
 action_type TEXT NOT NULL,asset_session_index INTEGER,
 raw_value REAL,currency TEXT,action_time_utc TEXT NOT NULL,
 action_observation_id TEXT NOT NULL,corporate_action_version_id TEXT NOT NULL,
 observed_at TEXT NOT NULL,available_at TEXT NOT NULL,
 availability_basis TEXT NOT NULL,observation_kind TEXT NOT NULL,
 observation_sequence INTEGER NOT NULL,normalized_action_json TEXT NOT NULL,
 economic_role TEXT NOT NULL,quality_status TEXT NOT NULL,
 PRIMARY KEY(asset_id,effective_trading_day,action_type)
) WITHOUT ROWID;
CREATE TABLE temporal_return_steps(
 asset_id INTEGER NOT NULL,asset_session_index INTEGER NOT NULL,
 trading_day TEXT NOT NULL,previous_trading_day TEXT,
 provider_close_previous REAL,provider_close_current REAL NOT NULL,
 cash_distribution REAL NOT NULL,split_factor_product REAL NOT NULL,
 action_count INTEGER NOT NULL,cash_action_count INTEGER NOT NULL,
 split_action_count INTEGER NOT NULL,economic_gross_factor REAL,
 log_economic_gross_factor REAL,provider_control_factor REAL,
 adjusted_close_audit_factor REAL,provider_reconciliation_error REAL,
 action_class TEXT NOT NULL,step_status TEXT NOT NULL,
 PRIMARY KEY(asset_id,asset_session_index),UNIQUE(asset_id,trading_day),
 FOREIGN KEY(asset_id,asset_session_index)
   REFERENCES temporal_price_points(asset_id,asset_session_index)
) WITHOUT ROWID;
CREATE TABLE temporal_outcomes(
 origin_id INTEGER NOT NULL REFERENCES temporal_origins(origin_id),
 tau_sessions INTEGER NOT NULL REFERENCES dataset_horizons(tau_sessions),
 target_trading_day TEXT,raw_close_return_pct REAL,total_return_pct REAL,
 corporate_action_overlap INTEGER NOT NULL CHECK(corporate_action_overlap IN (0,1)),
 cash_distribution_count INTEGER NOT NULL,split_action_count INTEGER NOT NULL,
 quarantined_step_count INTEGER NOT NULL,action_overlap_class TEXT NOT NULL,
 raw_close_label_status TEXT NOT NULL,total_return_label_status TEXT NOT NULL,
 label_version TEXT NOT NULL,PRIMARY KEY(origin_id,tau_sessions)
) WITHOUT ROWID;
CREATE TABLE coverage_by_horizon(
 tau_sessions INTEGER PRIMARY KEY,total_origins INTEGER NOT NULL,
 resolved_origins INTEGER NOT NULL,total_return_usable_origins INTEGER NOT NULL,
 action_quarantine_origins INTEGER NOT NULL,insufficient_future_origins INTEGER NOT NULL,
 no_action_origins INTEGER NOT NULL,cash_overlap_origins INTEGER NOT NULL,
 split_overlap_origins INTEGER NOT NULL,cash_and_split_origins INTEGER NOT NULL,
 v001_corporate_action_rows_recovered INTEGER NOT NULL,usable_fraction_resolved REAL
);
CREATE TABLE coverage_by_sector(
 tau_sessions INTEGER NOT NULL,sector TEXT NOT NULL,total_origins INTEGER NOT NULL,
 resolved_origins INTEGER NOT NULL,total_return_usable_origins INTEGER NOT NULL,
 action_quarantine_origins INTEGER NOT NULL,insufficient_future_origins INTEGER NOT NULL,
 v001_corporate_action_rows_recovered INTEGER NOT NULL,usable_fraction_resolved REAL,
 PRIMARY KEY(tau_sessions,sector)
);
CREATE TABLE coverage_by_year(
 tau_sessions INTEGER NOT NULL,origin_year TEXT NOT NULL,total_origins INTEGER NOT NULL,
 resolved_origins INTEGER NOT NULL,total_return_usable_origins INTEGER NOT NULL,
 action_quarantine_origins INTEGER NOT NULL,insufficient_future_origins INTEGER NOT NULL,
 v001_corporate_action_rows_recovered INTEGER NOT NULL,usable_fraction_resolved REAL,
 PRIMARY KEY(tau_sessions,origin_year)
);
CREATE TABLE training_gate(
 gate_name TEXT PRIMARY KEY,status TEXT NOT NULL,authorized INTEGER NOT NULL
   CHECK(authorized=0),reason TEXT NOT NULL
);
CREATE VIEW market_temporal_v002_outcomes AS
SELECT s.state_id,s.asset_id,s.ticker,s.sector,s.origin_trading_day,
       o.target_trading_day,o.tau_sessions,o.raw_close_return_pct,
       o.total_return_pct,o.corporate_action_overlap,o.cash_distribution_count,
       o.split_action_count,o.quarantined_step_count,o.action_overlap_class,
       o.raw_close_label_status,o.total_return_label_status,o.label_version
FROM temporal_outcomes o JOIN temporal_origins s USING(origin_id);
"""


def _decode(value: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return value


def _objects(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))
    }


def _metadata(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    return {
        str(row["key"]): _decode(row["value_json"])
        for row in conn.execute(f"SELECT key,value_json FROM {table}")
    }


def load_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict) or payload.get("version") != "market_temporal_dataset_v002":
        raise ValueError("unsupported_temporal_dataset_v002_version")
    source = payload.get("source_contract") or {}
    expected_source = {
        "source_asof_contract": "daily_price_asof_v1",
        "source_asof_mode": "historical_session_close_assumption",
        "state_clock": "exchange_session_close",
        "strict_historical_pit": False,
        "market_feature_version": "market_daily_state_v003_core",
        "core_label_version": "market_daily_reaction_v003_core",
        "temporal_v001_dataset_contract": "market_temporal_horizon_conditioned_outcomes_v001",
        "temporal_v001_label_version": "market_temporal_terminal_return_v001",
    }
    if any(source.get(key) != value for key, value in expected_source.items()):
        raise ValueError("unsupported_v002_source_contract")
    economic = payload.get("economic_return_contract") or {}
    expected_economic = {
        "cash_distribution_types": ["dividend", "capital_gain"],
        "split_action_type": "stock_split",
        "one_session_gross_factor": "(provider_close_t + cash_distribution_t) / provider_close_t_minus_1",
        "multi_session_gross_factor": "product of one-session gross factors over (origin,target]",
        "cash_reinvestment_policy": "cash distribution reinvested at effective-session close",
        "action_interval": "open_origin_closed_target",
        "provider_adjusted_close_target_allowed": False,
    }
    if any(economic.get(key) != value for key, value in expected_economic.items()):
        raise ValueError("unsupported_economic_return_contract")
    split_policy = economic.get("split_policy", "")
    if not split_policy.startswith("do_not_multiply_split_factor"):
        raise ValueError("split_factor_must_not_be_applied_to_provider_normalized_close")
    reconciliation = payload.get("provider_reconciliation_gate") or {}
    if (
        reconciliation.get("control_factor_no_cash")
        != "provider_close_t / provider_close_t_minus_1"
        or reconciliation.get("control_factor_with_cash")
        != "provider_close_t / (provider_close_t_minus_1 - cash_distribution_t)"
        or reconciliation.get("adjusted_close_never_enters_total_return_formula") is not True
        or not 0 < float(reconciliation.get("absolute_factor_tolerance", 0)) <= 1e-4
    ):
        raise ValueError("unsupported_provider_reconciliation_contract")
    materialization = payload.get("materialization_contract") or {}
    expected_materialization = {
        "output_db": "data/processed/market_temporal_v002.db",
        "dataset_contract": "market_temporal_horizon_conditioned_total_return_v002",
        "label_version": "market_temporal_total_shareholder_return_v002",
        "source_core_and_v001_are_read_only": True,
        "idempotent_rebuild": True,
        "atomic_publication": True,
    }
    if any(materialization.get(key) != value for key, value in expected_materialization.items()):
        raise ValueError("unsupported_v002_materialization_contract")
    horizon = payload.get("horizon_contract") or {}
    domain = horizon.get("tau_domain") or {}
    if domain != {
        "minimum_sessions": 1,
        "maximum_sessions": 252,
        "unit": "eligible_exchange_sessions",
        "integer_only": True,
    }:
        raise ValueError("unsupported_v002_tau_domain")
    if horizon.get("supported_materialization_strategies") != [
        "configured_sparse", "configured_plus", "dense_all"
    ]:
        raise ValueError("unsupported_v002_materialization_strategies")
    train = set(map(int, horizon.get("training_anchor_sessions", [])))
    holdout = set(map(int, horizon.get("temporal_generalization_holdout_sessions", [])))
    configured = set(map(int, horizon.get("materialized_sessions", [])))
    if train & holdout or configured != train | holdout:
        raise ValueError("invalid_v002_anchor_holdout_partition")
    if set(CORE_PARITY_HORIZONS) - configured:
        raise ValueError("v002_core_parity_horizons_missing")
    parity = payload.get("parity_gates") or {}
    if (
        float(parity.get("v001_all_materialized_taus", {}).get(
            "return_absolute_tolerance", -1
        )) != 1e-9
        or parity.get("v001_all_materialized_taus", {}).get(
            "require_zero_missing_rows"
        ) is not True
        or parity.get("no_action_identity", {}).get(
            "required_horizons_sessions"
        ) != list(CORE_PARITY_HORIZONS)
        or parity.get("no_action_identity", {}).get(
            "require_zero_mismatches"
        ) is not True
    ):
        raise ValueError("unsupported_v002_parity_contract")
    guards = payload.get("guards") or {}
    required_false = (
        "training_authorized", "model_training_performed",
        "v009_artifacts_loaded_or_modified", "source_market_db_mutation_allowed",
        "market_v003_core_mutation_allowed", "market_temporal_v001_mutation_allowed",
        "event_features_allowed", "graph_features_allowed", "random_split_allowed",
    )
    if any(guards.get(key) is not False for key in required_false):
        raise ValueError("v002_guard_mismatch")
    return payload


def economic_total_return_factor(
    previous_close: float, current_close: float, cash_distribution: float,
) -> float:
    """Economic close-to-close wealth factor for one share held through t."""
    if not all(math.isfinite(x) for x in (previous_close, current_close, cash_distribution)):
        raise ValueError("nonfinite_return_factor_input")
    if previous_close <= 0 or current_close <= 0 or cash_distribution < 0:
        raise ValueError("invalid_return_factor_input")
    factor = (current_close + cash_distribution) / previous_close
    if not math.isfinite(factor) or factor <= 0:
        raise ValueError("invalid_economic_gross_factor")
    return factor


def provider_adjustment_control_factor(
    previous_close: float, current_close: float, cash_distribution: float,
) -> float:
    """Yahoo-style control factor; audit-only and never the economic target."""
    denominator = previous_close - cash_distribution
    if not all(math.isfinite(x) for x in (previous_close, current_close, cash_distribution)):
        raise ValueError("nonfinite_provider_control_input")
    if denominator <= 0 or current_close <= 0 or cash_distribution < 0:
        raise ValueError("invalid_provider_control_denominator")
    return current_close / denominator


def compound_total_return_pct(log_factor_difference: float) -> float:
    value = 100.0 * math.expm1(log_factor_difference)
    if not math.isfinite(value):
        raise ValueError("nonfinite_compounded_total_return")
    return value


def _same_number(expected: Any, observed: Any, tolerance: float) -> bool:
    if expected is None or observed is None:
        return expected is None and observed is None
    return math.isclose(float(expected), float(observed), rel_tol=0.0, abs_tol=tolerance)


def _horizon_roles(cfg: dict[str, Any], tau: int) -> list[str]:
    horizon = cfg["horizon_contract"]
    roles: list[str] = []
    if tau in CORE_PARITY_HORIZONS:
        roles.append("core_no_action_identity")
    if tau in horizon["training_anchor_sessions"]:
        roles.append("training_anchor_checkpoint")
    if tau in horizon["temporal_generalization_holdout_sessions"]:
        roles.append("temporal_generalization_holdout")
    if tau in horizon["coverage_audit_sessions"]:
        roles.append("long_horizon_total_return_coverage_audit")
    return roles or ["auxiliary_tau"]


def validate_inputs(
    source: sqlite3.Connection, core: sqlite3.Connection,
    v001: sqlite3.Connection, cfg: dict[str, Any],
) -> tuple[dict[str, Any], dict[str, Any], str, list[int]]:
    required_source = {
        "assets", "daily_price_asof_configs", "corporate_action_versions",
        "corporate_action_observations",
    }
    if not required_source.issubset(_objects(source, "table")):
        raise ValueError("v002_source_schema_missing_tables")
    if QUALITY_VIEW not in _objects(source, "view"):
        raise ValueError("v002_source_quality_view_missing")
    price_columns = {str(row[1]) for row in source.execute(f"PRAGMA table_info({QUALITY_VIEW})")}
    if "observed_adjusted_close" not in price_columns:
        raise ValueError("v002_adjusted_close_audit_column_missing")
    version_columns = {
        str(row[1]) for row in source.execute("PRAGMA table_info(corporate_action_versions)")
    }
    observation_columns = {
        str(row[1]) for row in source.execute("PRAGMA table_info(corporate_action_observations)")
    }
    if not {
        "raw_value", "currency", "action_time_utc", "normalized_action_json",
        "is_present",
    }.issubset(version_columns) or not {
        "available_at", "availability_basis", "observation_kind",
        "observation_sequence",
    }.issubset(observation_columns):
        raise ValueError("v002_corporate_action_lineage_columns_missing")
    asof = source.execute(
        """SELECT selection_point_in_time_verified,cutoff_column,adjusted_close_role
           FROM daily_price_asof_configs
           WHERE asof_contract_version='daily_price_asof_v1'
             AND mode='historical_session_close_assumption'"""
    ).fetchall()
    if len(asof) != 1 or tuple(asof[0]) != (0, "available_at", "audit_only_not_identity"):
        raise ValueError("v002_source_asof_contract_mismatch")
    if not {
        "build_metadata", "market_daily_v003_states", "market_daily_v003_labels"
    }.issubset(_objects(core, "table")):
        raise ValueError("v002_core_schema_mismatch")
    core_meta = _metadata(core, "build_metadata")
    core_cfg = core_meta.get("config") or {}
    expected_core = {
        "source_asof_contract": "daily_price_asof_v1",
        "source_asof_mode": "historical_session_close_assumption",
        "state_clock": "exchange_session_close",
        "strict_historical_pit": False,
        "feature_version": "market_daily_state_v003_core",
        "label_version": "market_daily_reaction_v003_core",
        "target": "raw_close_t_to_raw_close_t_plus_h",
    }
    if any(core_cfg.get(key) != value for key, value in expected_core.items()):
        raise ValueError("v002_core_contract_mismatch")
    required_v001 = {
        "metadata", "dataset_horizons", "temporal_price_points", "temporal_origins",
        "temporal_corporate_actions", "temporal_outcomes", "training_gate",
    }
    if not required_v001.issubset(_objects(v001, "table")):
        raise ValueError("temporal_v001_schema_mismatch")
    if "market_temporal_v001_outcomes" not in _objects(v001, "view"):
        raise ValueError("temporal_v001_outcome_view_missing")
    v001_meta = _metadata(v001, "metadata")
    manifest = v001_meta.get("manifest")
    if not isinstance(manifest, dict):
        raise ValueError("temporal_v001_manifest_missing")
    expected_v001 = cfg["source_contract"]
    if (
        manifest.get("dataset_contract")
        != expected_v001["temporal_v001_dataset_contract"]
        or manifest.get("label_version") != expected_v001["temporal_v001_label_version"]
        or manifest.get("parity_status") != "PASS"
    ):
        raise ValueError("temporal_v001_manifest_not_eligible")
    gate = v001.execute(
        "SELECT status,authorized FROM training_gate WHERE gate_name='temporal_training'"
    ).fetchone()
    if gate is None or int(gate["authorized"]) != 0 or not str(gate["status"]).startswith("BLOCKED"):
        raise ValueError("temporal_v001_training_gate_unexpected")
    v001_taus = [
        int(row[0]) for row in v001.execute(
            "SELECT tau_sessions FROM dataset_horizons WHERE materialized=1 ORDER BY 1"
        )
    ]
    required_configured = set(map(int, cfg["horizon_contract"]["materialized_sessions"]))
    if not required_configured.issubset(v001_taus):
        raise ValueError("temporal_v001_missing_required_sparse_taus")
    cutoff = str(core_meta.get("state_last_day") or "")
    if not cutoff:
        cutoff = str(core.execute(
            "SELECT MAX(trading_day) FROM market_daily_v003_states"
        ).fetchone()[0])
    if str(manifest.get("source_cutoff_day")) != cutoff:
        raise ValueError("temporal_v001_core_cutoff_mismatch")
    core_states = int(core.execute("SELECT COUNT(*) FROM market_daily_v003_states").fetchone()[0])
    v001_origins = int(v001.execute("SELECT COUNT(*) FROM temporal_origins").fetchone()[0])
    if core_states != v001_origins:
        raise ValueError("temporal_v001_origin_count_mismatch")
    return core_meta, manifest, cutoff, v001_taus


def selected_prices_with_audit(
    source: sqlite3.Connection, asset_id: int, cutoff_day: str,
) -> list[dict[str, Any]]:
    rows = source.execute(
        f"""WITH eligible AS (
          SELECT g.asset_id,g.trading_day,g.bar_end_utc,g.close,
                 g.observed_adjusted_close,g.price_observation_id,
                 g.observation_sequence,g.observed_at,g.causal_available_at,
                 ROW_NUMBER() OVER (
                   PARTITION BY g.asset_id,g.trading_day
                   ORDER BY g.observation_sequence DESC,julianday(g.observed_at) DESC,
                            g.price_observation_id DESC
                 ) obs_rank
          FROM {QUALITY_VIEW} g JOIN assets a ON a.asset_id=g.asset_id
          WHERE a.active=1 AND a.asset_type='equity' AND g.interval='1d'
            AND g.asset_id=? AND g.trading_day<=?
            AND julianday(g.causal_available_at)<=julianday(g.bar_end_utc)
        ) SELECT * FROM eligible WHERE obs_rank=1 ORDER BY trading_day""",
        (asset_id, cutoff_day),
    ).fetchall()
    output = [dict(row) for row in rows]
    if len({row["trading_day"] for row in output}) != len(output):
        raise ValueError("duplicate_v002_selected_price_day")
    for row in output:
        close = row["close"]
        if not isinstance(close, (int, float)) or not math.isfinite(float(close)) or close <= 0:
            raise ValueError("invalid_v002_selected_close")
    return output


def latest_present_actions_with_values(
    source: sqlite3.Connection, asset_id: int, cutoff_day: str,
) -> list[dict[str, Any]]:
    return [dict(row) for row in source.execute(
        """WITH ranked AS (
          SELECT o.asset_id,o.effective_trading_day,o.action_type,
                 o.action_observation_id,o.corporate_action_version_id,
                 o.observed_at,o.available_at,o.availability_basis,
                 o.observation_kind,o.observation_sequence,
                 v.is_present,v.raw_value,v.currency,v.action_time_utc,
                 v.normalized_action_json,
                 ROW_NUMBER() OVER (
                   PARTITION BY o.asset_id,o.effective_trading_day,o.action_type
                   ORDER BY o.observation_sequence DESC,julianday(o.observed_at) DESC,
                            o.action_observation_id DESC
                 ) rn
          FROM corporate_action_observations o
          JOIN corporate_action_versions v
            ON v.corporate_action_version_id=o.corporate_action_version_id
          WHERE o.asset_id=? AND o.effective_trading_day<=?
        ) SELECT * FROM ranked WHERE rn=1 AND is_present=1
          ORDER BY effective_trading_day,action_type""",
        (asset_id, cutoff_day),
    )]


def _initial_parity(version: str, tolerance: float) -> dict[str, Any]:
    return {
        "version": version, "status": "PENDING", "return_absolute_tolerance": tolerance,
        "reference_rows": 0, "candidate_rows": 0, "compared_rows": 0,
        "missing_reference_rows": 0, "missing_candidate_rows": 0,
        "mismatch_counts_by_field": Counter(), "mismatch_samples": [],
    }


def _mismatch(
    report: dict[str, Any], *, state_id: str, asset_id: int, origin_day: str,
    tau: int, field: str, expected: Any, observed: Any, reason: str,
) -> None:
    report["mismatch_counts_by_field"][field] += 1
    if len(report["mismatch_samples"]) < 100:
        report["mismatch_samples"].append({
            "state_id": state_id, "asset_id": asset_id,
            "origin_trading_day": origin_day, "tau_sessions": tau,
            "field_name": field, "expected": expected, "observed": observed,
            "reason": reason,
        })


def _finalize_parity(report: dict[str, Any]) -> dict[str, Any]:
    report["mismatch_counts_by_field"] = dict(sorted(
        report["mismatch_counts_by_field"].items()
    ))
    failures = (
        report["missing_reference_rows"] + report["missing_candidate_rows"]
        + sum(report["mismatch_counts_by_field"].values())
    )
    report["status"] = "PASS" if failures == 0 else "FAIL"
    return report


def _new_action_audit(tolerance: float) -> dict[str, Any]:
    return {
        "version": "market_temporal_v002_action_reconciliation",
        "status": "PENDING", "provider_factor_absolute_tolerance": tolerance,
        "latest_present_actions": 0, "actions_by_type": Counter(),
        "null_currency_by_type": Counter(), "action_quality_status": Counter(),
        "daily_step_status": Counter(), "daily_action_class": Counter(),
        "provider_reconciliation_rows": 0, "provider_reconciliation_failures": 0,
        "maximum_provider_factor_error": 0.0, "mean_provider_factor_error": None,
        "economic_vs_provider_factor_gap_mean": None,
        "economic_vs_provider_factor_gap_maximum": 0.0,
        "economic_vs_provider_factor_gap_rows": 0,
        "mismatch_samples": [], "largest_cash_distribution_ratios": [],
        "split_factor_is_applied_to_return": False,
        "provider_adjusted_close_is_target": False,
        "cash_return_formula": "(close_t + distribution_t) / close_t_minus_1",
        "provider_control_formula": "close_t / (close_t_minus_1 - distribution_t)",
    }


def _record_action_failure(
    report: dict[str, Any], *, asset_id: int, trading_day: str,
    reason: str, details: dict[str, Any],
) -> None:
    report["provider_reconciliation_failures"] += 1
    if len(report["mismatch_samples"]) < 100:
        report["mismatch_samples"].append({
            "asset_id": asset_id, "trading_day": trading_day,
            "reason": reason, **details,
        })


def _finish_action_audit(
    report: dict[str, Any], error_sum: float, gap_sum: float,
) -> dict[str, Any]:
    pending_actions = int(report["action_quality_status"].get(
        "pending_provider_reconciliation", 0
    ))
    if pending_actions:
        report["provider_reconciliation_failures"] += pending_actions
        report["mismatch_samples"].append({
            "reason": "pending_provider_reconciliation_after_materialization",
            "action_rows": pending_actions,
        })
    rows = int(report["provider_reconciliation_rows"])
    report["mean_provider_factor_error"] = None if not rows else error_sum / rows
    gap_rows = int(report["economic_vs_provider_factor_gap_rows"])
    report["economic_vs_provider_factor_gap_mean"] = (
        None if not gap_rows else gap_sum / gap_rows
    )
    for key in (
        "actions_by_type", "null_currency_by_type", "action_quality_status",
        "daily_step_status", "daily_action_class",
    ):
        report[key] = dict(sorted(report[key].items()))
    report["largest_cash_distribution_ratios"] = sorted(
        report["largest_cash_distribution_ratios"],
        key=lambda row: row["cash_distribution_to_previous_close"], reverse=True,
    )[:100]
    report["status"] = (
        "PASS" if report["provider_reconciliation_failures"] == 0 else "FAIL"
    )
    return report


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", dir=path.parent)
    try:
        with os.fdopen(handle, "w", encoding="utf-8") as stream:
            stream.write(text)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temp_name, path)
    finally:
        Path(temp_name).unlink(missing_ok=True)


def _write_reports(
    report_dir: Path, *, manifest: dict[str, Any], v001_parity: dict[str, Any],
    no_action: dict[str, Any], action: dict[str, Any], coverage: dict[str, Any],
    audit: dict[str, Any], plan: dict[str, Any] | None = None,
) -> None:
    payloads = {
        "manifest.json": manifest,
        "v001_parity_report.json": v001_parity,
        "no_action_identity_report.json": no_action,
        "action_reconciliation_report.json": action,
        "coverage_report.json": coverage,
        "audit.json": audit,
    }
    if plan is not None:
        payloads["plan.json"] = plan
    for name, payload in payloads.items():
        _atomic_text(report_dir / name, json.dumps(
            payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False
        ) + "\n")
    summary = [
        "# Market Temporal V002", "",
        f"Integrity: **{audit.get('integrity_status', 'NOT_RUN')}**.",
        f"V001 parity: **{v001_parity.get('status', 'NOT_RUN')}**.",
        f"No-action identity: **{no_action.get('status', 'NOT_RUN')}**.",
        f"Action reconciliation: **{action.get('status', 'NOT_RUN')}**.", "",
        f"Origins: {audit.get('origin_rows', 0):,}; outcomes: {audit.get('outcome_rows', 0):,}.",
        f"Training gate: `{audit.get('training_gate_status', 'BLOCKED')}`.", "",
        "Adjusted Close was used only as a provider convention control; it is not the target.",
        "No model was trained and V009 was not opened or modified.", "",
    ]
    _atomic_text(report_dir / "SUMMARY.md", "\n".join(summary))


def _insert_coverage_tables(
    conn: sqlite3.Connection, audit_taus: Sequence[int],
) -> None:
    conn.execute("DELETE FROM coverage_by_horizon")
    conn.execute("DELETE FROM coverage_by_sector")
    conn.execute("DELETE FROM coverage_by_year")
    base = """
      COUNT(*) total_origins,
      SUM(o.total_return_label_status!='insufficient_future') resolved_origins,
      SUM(o.total_return_label_status='usable') total_return_usable_origins,
      SUM(o.total_return_label_status='action_data_quarantine') action_quarantine_origins,
      SUM(o.total_return_label_status='insufficient_future') insufficient_future_origins
    """
    conn.execute(f"""INSERT INTO coverage_by_horizon
      SELECT o.tau_sessions,{base},
      SUM(o.action_overlap_class='none') no_action_origins,
      SUM(o.action_overlap_class='cash') cash_overlap_origins,
      SUM(o.action_overlap_class='split') split_overlap_origins,
      SUM(o.action_overlap_class='cash_and_split') cash_and_split_origins,
      SUM(o.raw_close_label_status='corporate_action_overlap'
          AND o.total_return_label_status='usable') recovered,
      CASE WHEN SUM(o.total_return_label_status!='insufficient_future')=0 THEN NULL ELSE
        1.0*SUM(o.total_return_label_status='usable')/
        SUM(o.total_return_label_status!='insufficient_future') END usable_fraction
      FROM temporal_outcomes o GROUP BY o.tau_sessions""")
    placeholders = ",".join("?" for _ in audit_taus)
    conn.execute(f"""INSERT INTO coverage_by_sector
      SELECT o.tau_sessions,s.sector,{base},
      SUM(o.raw_close_label_status='corporate_action_overlap'
          AND o.total_return_label_status='usable') recovered,
      CASE WHEN SUM(o.total_return_label_status!='insufficient_future')=0 THEN NULL ELSE
        1.0*SUM(o.total_return_label_status='usable')/
        SUM(o.total_return_label_status!='insufficient_future') END usable_fraction
      FROM temporal_outcomes o JOIN temporal_origins s USING(origin_id)
      WHERE o.tau_sessions IN ({placeholders}) GROUP BY o.tau_sessions,s.sector""",
      tuple(audit_taus),
    )
    conn.execute(f"""INSERT INTO coverage_by_year
      SELECT o.tau_sessions,substr(s.origin_trading_day,1,4),{base},
      SUM(o.raw_close_label_status='corporate_action_overlap'
          AND o.total_return_label_status='usable') recovered,
      CASE WHEN SUM(o.total_return_label_status!='insufficient_future')=0 THEN NULL ELSE
        1.0*SUM(o.total_return_label_status='usable')/
        SUM(o.total_return_label_status!='insufficient_future') END usable_fraction
      FROM temporal_outcomes o JOIN temporal_origins s USING(origin_id)
      WHERE o.tau_sessions IN ({placeholders})
      GROUP BY o.tau_sessions,substr(s.origin_trading_day,1,4)""", tuple(audit_taus))


def _coverage_report(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": "market_temporal_v002_total_return_coverage_audit",
        "status": "REVIEW_REQUIRED_BEFORE_MODEL_PREREGISTRATION",
        "audit_horizons_sessions": cfg["horizon_contract"]["coverage_audit_sessions"],
        "by_horizon": [dict(row) for row in conn.execute(
            "SELECT * FROM coverage_by_horizon ORDER BY tau_sessions"
        )],
        "by_sector": [dict(row) for row in conn.execute(
            "SELECT * FROM coverage_by_sector ORDER BY tau_sessions,sector"
        )],
        "by_origin_year": [dict(row) for row in conn.execute(
            "SELECT * FROM coverage_by_year ORDER BY tau_sessions,origin_year"
        )],
        "training_authorized": False,
        "interpretation": (
            "Rows excluded by V001 solely for supported corporate actions may be recovered "
            "only when their action steps pass V002 provider-unit/timing reconciliation."
        ),
    }


def _configure_destination(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.executescript(SCHEMA)


def _read_output_manifest(path: Path) -> dict[str, Any]:
    with closing(ro_connect(path)) as conn:
        row = conn.execute("SELECT value_json FROM metadata WHERE key='manifest'").fetchone()
        if row is None:
            raise ValueError("v002_output_manifest_missing")
        return json.loads(str(row[0]))


def _compare_candidate(
    report: dict[str, Any], ref: dict[str, Any], candidate: dict[str, Any],
    state: dict[str, Any], tau: int, tolerance: float,
) -> None:
    fields = (
        ("target_trading_day", ref["target_trading_day"], candidate["target_trading_day"], False),
        ("raw_close_return_pct", ref["return_pct"], candidate["raw_close_return_pct"], True),
        ("corporate_action_overlap", ref["corporate_action_overlap"], candidate["corporate_action_overlap"], False),
        ("raw_close_label_status", ref["label_status"], candidate["raw_close_label_status"], False),
    )
    for field, expected, observed, numeric in fields:
        same = _same_number(expected, observed, tolerance) if numeric else expected == observed
        if not same:
            _mismatch(
                report, state_id=str(state["state_id"]), asset_id=int(state["asset_id"]),
                origin_day=str(state["origin_trading_day"]), tau=tau, field=field,
                expected=expected, observed=observed, reason="v001_value_mismatch",
            )


def replay_parity(
    output_db: Path, v001_db: Path, tolerance: float,
) -> tuple[dict[str, Any], dict[str, Any]]:
    v001_report = _initial_parity("market_temporal_v002_v001_full_parity", tolerance)
    no_action = _initial_parity("market_temporal_v002_no_action_identity", tolerance)
    no_action["required_horizons_sessions"] = list(CORE_PARITY_HORIZONS)
    with closing(ro_connect(output_db)) as out, closing(ro_connect(v001_db)) as v001:
        v001_taus = [int(row[0]) for row in v001.execute(
            "SELECT tau_sessions FROM dataset_horizons WHERE materialized=1 ORDER BY 1"
        )]
        asset_ids = [int(row[0]) for row in out.execute(
            "SELECT DISTINCT asset_id FROM temporal_origins ORDER BY asset_id"
        )]
        for asset_id in asset_ids:
            states = {
                int(row["origin_id"]): dict(row) for row in out.execute(
                    "SELECT origin_id,state_id,asset_id,origin_trading_day "
                    "FROM temporal_origins WHERE asset_id=?", (asset_id,),
                )
            }
            refs = {
                (int(row["origin_id"]), int(row["tau_sessions"])): dict(row)
                for row in v001.execute(
                    "SELECT o.origin_id,o.tau_sessions,o.target_trading_day,o.return_pct,"
                    "o.corporate_action_overlap,o.label_status "
                    "FROM temporal_outcomes o JOIN temporal_origins s USING(origin_id) "
                    "WHERE s.asset_id=?", (asset_id,),
                )
            }
            candidates = {
                (int(row["origin_id"]), int(row["tau_sessions"])): dict(row)
                for row in out.execute(
                    "SELECT o.origin_id,o.tau_sessions,o.target_trading_day,"
                    "o.raw_close_return_pct,o.total_return_pct,o.corporate_action_overlap,"
                    "o.raw_close_label_status,o.total_return_label_status "
                    "FROM temporal_outcomes o JOIN temporal_origins s USING(origin_id) "
                    "WHERE s.asset_id=? AND o.tau_sessions IN ("
                    + ",".join("?" for _ in v001_taus) + ")",
                    (asset_id, *v001_taus),
                )
            }
            v001_report["reference_rows"] += len(refs)
            v001_report["candidate_rows"] += len(candidates)
            v001_report["missing_reference_rows"] += len(set(candidates) - set(refs))
            v001_report["missing_candidate_rows"] += len(set(refs) - set(candidates))
            for key in sorted(set(refs) & set(candidates)):
                v001_report["compared_rows"] += 1
                _compare_candidate(
                    v001_report, refs[key], candidates[key], states[key[0]], key[1], tolerance
                )
                if key[1] in CORE_PARITY_HORIZONS and refs[key]["label_status"] == "usable":
                    no_action["reference_rows"] += 1
                    no_action["candidate_rows"] += 1
                    no_action["compared_rows"] += 1
                    if candidates[key]["total_return_label_status"] != "usable":
                        _mismatch(
                            no_action, state_id=str(states[key[0]]["state_id"]),
                            asset_id=asset_id,
                            origin_day=str(states[key[0]]["origin_trading_day"]), tau=key[1],
                            field="total_return_label_status", expected="usable",
                            observed=candidates[key]["total_return_label_status"],
                            reason="no_action_status_mismatch",
                        )
                    if not _same_number(
                        refs[key]["return_pct"], candidates[key]["total_return_pct"], tolerance
                    ):
                        _mismatch(
                            no_action, state_id=str(states[key[0]]["state_id"]),
                            asset_id=asset_id,
                            origin_day=str(states[key[0]]["origin_trading_day"]), tau=key[1],
                            field="total_return_pct", expected=refs[key]["return_pct"],
                            observed=candidates[key]["total_return_pct"],
                            reason="no_action_return_identity_mismatch",
                        )
    return _finalize_parity(v001_report), _finalize_parity(no_action)


def audit_output(
    output_db: Path, v001_db: Path, cfg: dict[str, Any], *, replay: bool = True,
) -> dict[str, Any]:
    failures: list[str] = []
    with closing(ro_connect(output_db)) as conn:
        required = {
            "metadata", "dataset_horizons", "temporal_price_points", "temporal_origins",
            "temporal_corporate_actions", "temporal_return_steps", "temporal_outcomes",
            "coverage_by_horizon", "coverage_by_sector", "coverage_by_year", "training_gate",
        }
        if not required.issubset(_objects(conn, "table")):
            raise ValueError("v002_output_schema_incomplete")
        meta = _metadata(conn, "metadata")
        manifest = meta.get("manifest") or {}
        stored_v001 = meta.get("v001_parity") or {}
        stored_no_action = meta.get("no_action_identity") or {}
        stored_action = meta.get("action_reconciliation") or {}
        origin_rows = int(conn.execute("SELECT COUNT(*) FROM temporal_origins").fetchone()[0])
        outcome_rows = int(conn.execute("SELECT COUNT(*) FROM temporal_outcomes").fetchone()[0])
        price_rows = int(conn.execute("SELECT COUNT(*) FROM temporal_price_points").fetchone()[0])
        step_rows = int(conn.execute("SELECT COUNT(*) FROM temporal_return_steps").fetchone()[0])
        action_rows = int(conn.execute("SELECT COUNT(*) FROM temporal_corporate_actions").fetchone()[0])
        taus = [int(row[0]) for row in conn.execute(
            "SELECT tau_sessions FROM dataset_horizons WHERE materialized=1 ORDER BY 1"
        )]
        if outcome_rows != origin_rows * len(taus):
            failures.append("OUTCOME_CARTESIAN_COVERAGE_MISMATCH")
        if step_rows != price_rows:
            failures.append("RETURN_STEP_PRICE_GRID_MISMATCH")
        bad_status = int(conn.execute(
            "SELECT COUNT(*) FROM temporal_outcomes WHERE total_return_label_status NOT IN "
            "('usable','action_data_quarantine','insufficient_future')"
        ).fetchone()[0])
        if bad_status:
            failures.append("UNKNOWN_TOTAL_RETURN_LABEL_STATUS")
        bad_insufficient = int(conn.execute(
            "SELECT COUNT(*) FROM temporal_outcomes WHERE total_return_label_status="
            "'insufficient_future' AND (target_trading_day IS NOT NULL OR "
            "raw_close_return_pct IS NOT NULL OR total_return_pct IS NOT NULL)"
        ).fetchone()[0])
        if bad_insufficient:
            failures.append("INSUFFICIENT_FUTURE_ROW_HAS_TARGET")
        bad_quarantine = int(conn.execute(
            "SELECT COUNT(*) FROM temporal_outcomes WHERE total_return_label_status="
            "'action_data_quarantine' AND (total_return_pct IS NOT NULL OR quarantined_step_count=0)"
        ).fetchone()[0])
        if bad_quarantine:
            failures.append("ACTION_QUARANTINE_ROW_INVARIANT_FAILED")
        bad_usable = int(conn.execute(
            "SELECT COUNT(*) FROM temporal_outcomes WHERE total_return_label_status='usable' "
            "AND (total_return_pct IS NULL OR quarantined_step_count!=0)"
        ).fetchone()[0])
        if bad_usable:
            failures.append("USABLE_TOTAL_RETURN_ROW_INVARIANT_FAILED")
        step_failures = int(conn.execute(
            "SELECT COUNT(*) FROM temporal_return_steps WHERE asset_session_index>0 AND "
            "(step_status LIKE 'quarantined_%' OR provider_reconciliation_error>? )",
            (float(cfg["provider_reconciliation_gate"]["absolute_factor_tolerance"]),),
        ).fetchone()[0])
        if step_failures:
            failures.append("PROVIDER_ACTION_RECONCILIATION_FAILED")
        gate = conn.execute(
            "SELECT status,authorized FROM training_gate WHERE gate_name='temporal_v002_training'"
        ).fetchone()
        if gate is None or int(gate["authorized"]) != 0:
            failures.append("V002_TRAINING_GATE_MISMATCH")
        coverage = _coverage_report(conn, cfg)
    if replay:
        v001_parity, no_action = replay_parity(
            output_db, v001_db,
            float(cfg["parity_gates"]["v001_all_materialized_taus"][
                "return_absolute_tolerance"
            ]),
        )
    else:
        v001_parity, no_action = stored_v001, stored_no_action
    if v001_parity.get("status") != "PASS":
        failures.append("V001_FULL_PARITY_FAILED")
    if no_action.get("status") != "PASS":
        failures.append("NO_ACTION_IDENTITY_FAILED")
    if stored_action.get("status") != "PASS":
        failures.append("ACTION_RECONCILIATION_REPORT_FAILED")
    return {
        "version": "market_temporal_v002_integrity_audit",
        "integrity_status": "PASS" if not failures else "FAIL",
        "failures": sorted(set(failures)), "origin_rows": origin_rows,
        "price_grid_rows": price_rows, "return_step_rows": step_rows,
        "corporate_action_rows": action_rows, "outcome_rows": outcome_rows,
        "materialized_taus": taus, "v001_parity": v001_parity,
        "no_action_identity": no_action, "action_reconciliation": stored_action,
        "coverage": coverage,
        "training_gate_status": None if gate is None else gate["status"],
        "training_authorized": False, "model_training_performed": False,
        "v009_loaded_or_modified": False,
        "manifest_build_fingerprint": manifest.get("build_fingerprint"),
    }


def materialize(
    source_db: Path = DEFAULT_SOURCE, core_db: Path = DEFAULT_CORE,
    v001_db: Path = DEFAULT_V001, output_db: Path = DEFAULT_OUTPUT,
    config_path: Path = DEFAULT_CONFIG, report_dir: Path = DEFAULT_REPORT_DIR,
    *, strategy: str | None = None, extra_taus: Sequence[int] = (),
    force_rebuild: bool = False,
) -> dict[str, Any]:
    resolved_paths = [
        source_db.resolve(), core_db.resolve(), v001_db.resolve(), output_db.resolve()
    ]
    if len(set(resolved_paths)) != 4:
        raise ValueError("source_core_v001_and_v002_output_must_be_distinct")
    if any("v009" in str(path).lower() for path in resolved_paths):
        raise ValueError("v009_artifacts_are_out_of_scope")
    cfg = load_contract(config_path)
    selected_strategy, taus = resolve_taus(cfg, strategy, extra_taus)
    source_before = file_state(source_db)
    core_before = file_state(core_db)
    v001_before = file_state(v001_db)
    code_sha, config_sha = file_digest(Path(__file__)), file_digest(config_path)
    build_fingerprint = digest({
        "contract": cfg, "strategy": selected_strategy, "taus": taus,
        "source_file_state": source_before, "core_file_state": core_before,
        "v001_file_state": v001_before, "code_sha256": code_sha,
        "config_sha256": config_sha,
    })
    if output_db.exists() and not force_rebuild:
        previous = _read_output_manifest(output_db)
        if previous.get("build_fingerprint") != build_fingerprint:
            raise ValueError("existing_v002_output_differs_use_force_rebuild")
        audit = audit_output(output_db, v001_db, cfg, replay=True)
        manifest = previous | {"dataset_sha256": file_digest(output_db), "reused_existing": True}
        _write_reports(
            report_dir, manifest=manifest, v001_parity=audit["v001_parity"],
            no_action=audit["no_action_identity"], action=audit["action_reconciliation"],
            coverage=audit["coverage"], audit=audit,
        )
        if audit["integrity_status"] != "PASS":
            raise BuildBlocked("existing Temporal Dataset V002 failed integrity audit")
        return audit | {"reused_existing": True}

    output_db.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=output_db.name + ".", suffix=".tmp", dir=output_db.parent
    )
    os.close(handle)
    temp_db = Path(temp_name)
    temp_db.unlink()
    tolerance = float(cfg["parity_gates"]["v001_all_materialized_taus"][
        "return_absolute_tolerance"
    ])
    provider_tolerance = float(
        cfg["provider_reconciliation_gate"]["absolute_factor_tolerance"]
    )
    v001_parity = _initial_parity("market_temporal_v002_v001_full_parity", tolerance)
    no_action = _initial_parity("market_temporal_v002_no_action_identity", tolerance)
    no_action["required_horizons_sessions"] = list(CORE_PARITY_HORIZONS)
    action_audit = _new_action_audit(provider_tolerance)
    coverage: dict[str, Any] = {"status": "NOT_RUN"}
    manifest: dict[str, Any] = {
        "dataset_contract": cfg["materialization_contract"]["dataset_contract"],
        "label_version": cfg["materialization_contract"]["label_version"],
        "build_fingerprint": build_fingerprint, "strategy": selected_strategy,
        "materialized_taus": taus,
        "configured_training_anchors": cfg["horizon_contract"]["training_anchor_sessions"],
        "configured_generalization_holdouts": cfg["horizon_contract"][
            "temporal_generalization_holdout_sessions"
        ],
        "source_db": str(source_db.resolve()), "core_db": str(core_db.resolve()),
        "temporal_v001_db": str(v001_db.resolve()),
        "source_file_state_before": source_before,
        "core_file_state_before": core_before, "v001_file_state_before": v001_before,
        "config_sha256": config_sha, "code_sha256": code_sha,
        "model_version": "NONE_DATA_PREPARATION_ONLY", "training_authorized": False,
        "v009_loaded_or_modified": False,
    }
    error_sum = 0.0
    economic_provider_gap_sum = 0.0
    try:
        with closing(ro_connect(source_db)) as source, closing(ro_connect(core_db)) as core, closing(
            ro_connect(v001_db)
        ) as v001, closing(sqlite3.connect(temp_db)) as dest:
            dest.row_factory = sqlite3.Row
            _configure_destination(dest)
            core_meta, v001_manifest, cutoff_day, v001_taus = validate_inputs(
                source, core, v001, cfg
            )
            manifest.update({
                "core_build_metadata": core_meta,
                "temporal_v001_build_fingerprint": v001_manifest["build_fingerprint"],
                "source_cutoff_day": cutoff_day,
                "source_gate_status": "READY_FOR_TOTAL_RETURN_MATERIALIZATION",
            })
            for tau in range(1, 253):
                dest.execute(
                    "INSERT INTO dataset_horizons VALUES(?,?,?)",
                    (tau, int(tau in taus), canonical(_horizon_roles(cfg, tau))),
                )
            asset_ids = [int(row[0]) for row in v001.execute(
                "SELECT DISTINCT asset_id FROM temporal_origins ORDER BY asset_id"
            )]
            outcome_rows_total = 0
            label_version = cfg["materialization_contract"]["label_version"]
            for asset_number, asset_id in enumerate(asset_ids, start=1):
                v1_prices = [dict(row) for row in v001.execute(
                    "SELECT * FROM temporal_price_points WHERE asset_id=? "
                    "ORDER BY asset_session_index", (asset_id,),
                )]
                source_prices = selected_prices_with_audit(source, asset_id, cutoff_day)
                if len(v1_prices) != len(source_prices):
                    raise ValueError("v001_v002_price_grid_length_mismatch")
                for index, (left, right) in enumerate(zip(v1_prices, source_prices)):
                    identity = (
                        int(left["asset_session_index"]) == index
                        and str(left["trading_day"]) == str(right["trading_day"])
                        and str(left["price_observation_id"]) == str(right["price_observation_id"])
                        and _same_number(left["raw_close"], right["close"], 0.0)
                    )
                    if not identity:
                        raise ValueError("v001_v002_price_grid_identity_mismatch")
                day_to_index = {
                    str(row["trading_day"]): index for index, row in enumerate(source_prices)
                }
                dest.executemany(
                    "INSERT INTO temporal_price_points VALUES(?,?,?,?,?,?,?,?,?,?)",
                    [(
                        asset_id, index, row["trading_day"], row["bar_end_utc"],
                        float(row["close"]),
                        None if row["observed_adjusted_close"] is None else float(
                            row["observed_adjusted_close"]
                        ),
                        str(row["price_observation_id"]), int(row["observation_sequence"]),
                        str(row["observed_at"]), str(row["causal_available_at"]),
                    ) for index, row in enumerate(source_prices)],
                )
                actions = latest_present_actions_with_values(source, asset_id, cutoff_day)
                v1_action_rows = [dict(row) for row in v001.execute(
                    "SELECT * FROM temporal_corporate_actions WHERE asset_id=? "
                    "AND effective_trading_day<=? ORDER BY effective_trading_day,action_type",
                    (asset_id, cutoff_day),
                )]
                source_action_identity = {
                    (str(row["effective_trading_day"]), str(row["action_type"])): (
                        str(row["action_observation_id"]),
                        str(row["corporate_action_version_id"]),
                    ) for row in actions
                }
                v1_action_identity = {
                    (str(row["effective_trading_day"]), str(row["action_type"])): (
                        str(row["action_observation_id"]),
                        str(row["corporate_action_version_id"]),
                    ) for row in v1_action_rows
                }
                if source_action_identity != v1_action_identity:
                    raise ValueError("v001_v002_action_lineage_mismatch")
                actions_by_day: dict[str, list[dict[str, Any]]] = defaultdict(list)
                action_dest_rows = []
                for row in actions:
                    action_type = str(row["action_type"])
                    day = str(row["effective_trading_day"])
                    action_audit["latest_present_actions"] += 1
                    action_audit["actions_by_type"][action_type] += 1
                    if row["currency"] is None:
                        action_audit["null_currency_by_type"][action_type] += 1
                    raw_value = row["raw_value"]
                    exact_index = day_to_index.get(day)
                    if action_type not in SUPPORTED_ACTION_TYPES:
                        quality = "quarantined_unsupported_action_type"
                    elif not isinstance(raw_value, (int, float)) or not math.isfinite(
                        float(raw_value)
                    ) or float(raw_value) <= 0:
                        quality = "quarantined_invalid_action_value"
                    elif exact_index is None and source_prices and (
                        str(source_prices[0]["trading_day"]) <= day
                        <= str(source_prices[-1]["trading_day"])
                    ):
                        quality = "quarantined_no_exact_price_session"
                    elif exact_index is None:
                        quality = "outside_selected_price_grid"
                    elif exact_index == 0:
                        # No outcome can cross into the first stored session because
                        # every origin is on/after it and the action interval is open
                        # at origin.  There is also no previous close for a step audit.
                        quality = "outside_outcome_window_grid_start"
                    else:
                        quality = "pending_provider_reconciliation"
                        actions_by_day[day].append(row)
                    action_audit["action_quality_status"][quality] += 1
                    economic_role = (
                        "cash_distribution" if action_type in CASH_ACTION_TYPES else
                        "split_lineage_provider_normalized_close" if action_type == "stock_split"
                        else "unsupported"
                    )
                    action_dest_rows.append((
                        asset_id, day, action_type, exact_index,
                        None if raw_value is None else float(raw_value), row["currency"],
                        str(row["action_time_utc"]), str(row["action_observation_id"]),
                        str(row["corporate_action_version_id"]), str(row["observed_at"]),
                        str(row["available_at"]), str(row["availability_basis"]),
                        str(row["observation_kind"]), int(row["observation_sequence"]),
                        str(row["normalized_action_json"]), economic_role, quality,
                    ))
                    if quality.startswith("quarantined_"):
                        _record_action_failure(
                            action_audit, asset_id=asset_id, trading_day=day,
                            reason=quality, details={"action_type": action_type,
                                                     "raw_value": raw_value},
                        )
                dest.executemany(
                    "INSERT INTO temporal_corporate_actions VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    action_dest_rows,
                )

                prefix_log = [0.0] * len(source_prices)
                prefix_bad = [0] * len(source_prices)
                prefix_cash = [0] * len(source_prices)
                prefix_split = [0] * len(source_prices)
                step_rows = []
                for index, price in enumerate(source_prices):
                    day = str(price["trading_day"])
                    current_close = float(price["close"])
                    day_actions = actions_by_day.get(day, [])
                    cash_rows = [row for row in day_actions if row["action_type"] in CASH_ACTION_TYPES]
                    split_rows = [row for row in day_actions if row["action_type"] == "stock_split"]
                    cash = sum(float(row["raw_value"]) for row in cash_rows)
                    split_product = math.prod(float(row["raw_value"]) for row in split_rows) or 1.0
                    action_count = len(day_actions)
                    action_class = (
                        "cash_and_split" if cash_rows and split_rows else
                        "cash" if cash_rows else "split" if split_rows else "none"
                    )
                    action_audit["daily_action_class"][action_class] += 1
                    if index == 0:
                        step_status = "grid_start"
                        previous_day = None
                        previous_close = None
                        economic_factor = log_factor = provider_control = None
                        adjusted_factor = provider_error = None
                    else:
                        previous = source_prices[index - 1]
                        previous_day = str(previous["trading_day"])
                        previous_close = float(previous["close"])
                        adjusted_previous = previous["observed_adjusted_close"]
                        adjusted_current = price["observed_adjusted_close"]
                        economic_factor = log_factor = provider_control = None
                        adjusted_factor = provider_error = None
                        try:
                            economic_factor = economic_total_return_factor(
                                previous_close, current_close, cash
                            )
                            provider_control = provider_adjustment_control_factor(
                                previous_close, current_close, cash
                            )
                            if (
                                adjusted_previous is None or adjusted_current is None
                                or not math.isfinite(float(adjusted_previous))
                                or not math.isfinite(float(adjusted_current))
                                or float(adjusted_previous) <= 0 or float(adjusted_current) <= 0
                            ):
                                raise ValueError("missing_adjusted_close_audit_value")
                            adjusted_factor = float(adjusted_current) / float(adjusted_previous)
                            provider_error = abs(provider_control - adjusted_factor)
                            if provider_error > provider_tolerance:
                                raise ValueError("provider_factor_reconciliation_tolerance_exceeded")
                            log_factor = math.log(economic_factor)
                            step_status = (
                                "usable_cash_and_split" if cash_rows and split_rows else
                                "usable_cash_distribution" if cash_rows else
                                "usable_split_normalized" if split_rows else "usable_no_action"
                            )
                        except ValueError as exc:
                            economic_factor = log_factor = None
                            step_status = "quarantined_provider_reconciliation"
                            _record_action_failure(
                                action_audit, asset_id=asset_id, trading_day=day,
                                reason=str(exc), details={
                                    "previous_close": previous_close,
                                    "current_close": current_close,
                                    "cash_distribution": cash,
                                    "provider_control_factor": provider_control,
                                    "adjusted_close_audit_factor": adjusted_factor,
                                    "absolute_factor_error": provider_error,
                                },
                            )
                        if provider_error is not None and math.isfinite(float(provider_error)):
                            action_audit["provider_reconciliation_rows"] += 1
                            error_sum += float(provider_error)
                            action_audit["maximum_provider_factor_error"] = max(
                                action_audit["maximum_provider_factor_error"], float(provider_error)
                            )
                        if economic_factor is not None and provider_control is not None:
                            gap = abs(economic_factor - provider_control)
                            economic_provider_gap_sum += gap
                            action_audit["economic_vs_provider_factor_gap_rows"] += 1
                            action_audit["economic_vs_provider_factor_gap_maximum"] = max(
                                action_audit["economic_vs_provider_factor_gap_maximum"], gap
                            )
                        if cash > 0 and previous_close > 0:
                            action_audit["largest_cash_distribution_ratios"].append({
                                "asset_id": asset_id, "trading_day": day,
                                "cash_distribution": cash, "previous_close": previous_close,
                                "cash_distribution_to_previous_close": cash / previous_close,
                                "economic_gross_factor": economic_factor,
                                "provider_control_factor": provider_control,
                            })
                    action_audit["daily_step_status"][step_status] += 1
                    if index > 0:
                        prefix_log[index] = prefix_log[index - 1] + (log_factor or 0.0)
                        prefix_bad[index] = prefix_bad[index - 1] + int(
                            step_status.startswith("quarantined_")
                        )
                        prefix_cash[index] = prefix_cash[index - 1] + len(cash_rows)
                        prefix_split[index] = prefix_split[index - 1] + len(split_rows)
                    step_rows.append((
                        asset_id, index, day, previous_day, previous_close, current_close,
                        cash, split_product, action_count, len(cash_rows), len(split_rows),
                        economic_factor, log_factor, provider_control, adjusted_factor,
                        provider_error, action_class, step_status,
                    ))
                    if action_count and step_status.startswith("usable_"):
                        for row in day_actions:
                            dest.execute(
                                "UPDATE temporal_corporate_actions SET quality_status="
                                "'provider_reconciled' WHERE asset_id=? AND "
                                "effective_trading_day=? AND action_type=?",
                                (asset_id, day, row["action_type"]),
                            )
                            action_audit["action_quality_status"][
                                "pending_provider_reconciliation"
                            ] -= 1
                            action_audit["action_quality_status"]["provider_reconciled"] += 1
                    elif action_count and step_status.startswith("quarantined_"):
                        for row in day_actions:
                            dest.execute(
                                "UPDATE temporal_corporate_actions SET quality_status="
                                "'quarantined_provider_reconciliation' WHERE asset_id=? AND "
                                "effective_trading_day=? AND action_type=?",
                                (asset_id, day, row["action_type"]),
                            )
                            action_audit["action_quality_status"][
                                "pending_provider_reconciliation"
                            ] -= 1
                            action_audit["action_quality_status"][
                                "quarantined_provider_reconciliation"
                            ] += 1
                dest.executemany(
                    "INSERT INTO temporal_return_steps VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    step_rows,
                )

                origins = [dict(row) for row in v001.execute(
                    "SELECT * FROM temporal_origins WHERE asset_id=? ORDER BY origin_id",
                    (asset_id,),
                )]
                dest.executemany(
                    "INSERT INTO temporal_origins VALUES(?,?,?,?,?,?,?,?,?,?)",
                    [(
                        row["origin_id"], row["state_id"], row["asset_id"], row["ticker"],
                        row["sector"], row["origin_trading_day"], row["state_time"],
                        row["origin_session_index"], row["raw_close_origin"],
                        row["market_feature_version"],
                    ) for row in origins],
                )
                refs = {
                    (int(row["origin_id"]), int(row["tau_sessions"])): dict(row)
                    for row in v001.execute(
                        "SELECT o.origin_id,o.tau_sessions,o.target_trading_day,o.return_pct,"
                        "o.corporate_action_overlap,o.label_status "
                        "FROM temporal_outcomes o JOIN temporal_origins s USING(origin_id) "
                        "WHERE s.asset_id=?", (asset_id,),
                    )
                }
                v001_parity["reference_rows"] += len(refs)
                candidate_v1_keys: set[tuple[int, int]] = set()
                outcome_rows = []
                for origin in origins:
                    origin_id = int(origin["origin_id"])
                    origin_index = int(origin["origin_session_index"])
                    state = {
                        "state_id": origin["state_id"], "asset_id": asset_id,
                        "origin_trading_day": origin["origin_trading_day"],
                    }
                    for tau in taus:
                        target_index = origin_index + tau
                        if target_index >= len(source_prices):
                            target_day = raw_return = total_return = None
                            overlap = cash_count = split_count = bad_count = 0
                            overlap_class = "insufficient_future"
                            raw_status = total_status = "insufficient_future"
                        else:
                            target_day = str(source_prices[target_index]["trading_day"])
                            raw_return = 100.0 * (
                                float(source_prices[target_index]["close"])
                                / float(source_prices[origin_index]["close"]) - 1.0
                            )
                            cash_count = prefix_cash[target_index] - prefix_cash[origin_index]
                            split_count = prefix_split[target_index] - prefix_split[origin_index]
                            bad_count = prefix_bad[target_index] - prefix_bad[origin_index]
                            overlap = int(cash_count + split_count > 0)
                            overlap_class = (
                                "cash_and_split" if cash_count and split_count else
                                "cash" if cash_count else "split" if split_count else "none"
                            )
                            raw_status = "corporate_action_overlap" if overlap else "usable"
                            if bad_count:
                                total_return, total_status = None, "action_data_quarantine"
                            else:
                                total_return = compound_total_return_pct(
                                    prefix_log[target_index] - prefix_log[origin_index]
                                )
                                total_status = "usable"
                        candidate = {
                            "target_trading_day": target_day,
                            "raw_close_return_pct": raw_return,
                            "corporate_action_overlap": overlap,
                            "raw_close_label_status": raw_status,
                        }
                        key = (origin_id, tau)
                        if key in refs:
                            candidate_v1_keys.add(key)
                            v001_parity["candidate_rows"] += 1
                            v001_parity["compared_rows"] += 1
                            _compare_candidate(
                                v001_parity, refs[key], candidate, state, tau, tolerance
                            )
                            if tau in CORE_PARITY_HORIZONS and refs[key]["label_status"] == "usable":
                                no_action["reference_rows"] += 1
                                no_action["candidate_rows"] += 1
                                no_action["compared_rows"] += 1
                                if total_status != "usable":
                                    _mismatch(
                                        no_action, state_id=str(state["state_id"]),
                                        asset_id=asset_id,
                                        origin_day=str(state["origin_trading_day"]), tau=tau,
                                        field="total_return_label_status", expected="usable",
                                        observed=total_status,
                                        reason="no_action_status_mismatch",
                                    )
                                if not _same_number(
                                    refs[key]["return_pct"], total_return, tolerance
                                ):
                                    _mismatch(
                                        no_action, state_id=str(state["state_id"]),
                                        asset_id=asset_id,
                                        origin_day=str(state["origin_trading_day"]), tau=tau,
                                        field="total_return_pct", expected=refs[key]["return_pct"],
                                        observed=total_return,
                                        reason="no_action_return_identity_mismatch",
                                    )
                        outcome_rows.append((
                            origin_id, tau, target_day, raw_return, total_return, overlap,
                            cash_count, split_count, bad_count, overlap_class, raw_status,
                            total_status, label_version,
                        ))
                missing_candidates = set(refs) - candidate_v1_keys
                v001_parity["missing_candidate_rows"] += len(missing_candidates)
                for origin_id, tau in sorted(missing_candidates)[:max(
                    0, 100 - len(v001_parity["mismatch_samples"])
                )]:
                    origin = next(row for row in origins if int(row["origin_id"]) == origin_id)
                    _mismatch(
                        v001_parity, state_id=str(origin["state_id"]), asset_id=asset_id,
                        origin_day=str(origin["origin_trading_day"]), tau=tau, field="row",
                        expected="V002 candidate row", observed=None,
                        reason="missing_candidate_row",
                    )
                dest.executemany(
                    "INSERT INTO temporal_outcomes VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)",
                    outcome_rows,
                )
                outcome_rows_total += len(outcome_rows)
                if asset_number % 25 == 0 or asset_number == len(asset_ids):
                    print(canonical({
                        "stage": "materialize_v002", "assets_complete": asset_number,
                        "assets_total": len(asset_ids), "outcomes": outcome_rows_total,
                    }), flush=True)

            v001_parity = _finalize_parity(v001_parity)
            no_action = _finalize_parity(no_action)
            action_audit = _finish_action_audit(
                action_audit, error_sum, economic_provider_gap_sum
            )
            _insert_coverage_tables(dest, cfg["horizon_contract"]["coverage_audit_sessions"])
            coverage = _coverage_report(dest, cfg)
            hard_gate_pass = (
                v001_parity["status"] == "PASS"
                and no_action["status"] == "PASS"
                and action_audit["status"] == "PASS"
            )
            gate_status = (
                "BLOCKED_PENDING_V002_FULL_ACTION_REVIEW" if hard_gate_pass
                else "BLOCKED_V002_DATA_GATE_FAILURE"
            )
            gate_reason = (
                "V002 data gates passed; training remains blocked until the full real reports "
                "are reviewed and a separate horizon-conditioned model protocol is preregistered."
                if hard_gate_pass else
                "V002 parity or corporate-action reconciliation failed; training is prohibited."
            )
            dest.execute(
                "INSERT INTO training_gate VALUES('temporal_v002_training',?,0,?)",
                (gate_status, gate_reason),
            )
            source_after, core_after, v001_after = (
                file_state(source_db), file_state(core_db), file_state(v001_db)
            )
            stable = (
                source_before == source_after and core_before == core_after
                and v001_before == v001_after
            )
            manifest.update({
                "source_file_state_after": source_after,
                "core_file_state_after": core_after, "v001_file_state_after": v001_after,
                "inputs_stable_during_build": stable,
                "source_core_and_v001_opened_read_only": True,
                "origin_rows": int(dest.execute(
                    "SELECT COUNT(*) FROM temporal_origins"
                ).fetchone()[0]),
                "price_grid_rows": int(dest.execute(
                    "SELECT COUNT(*) FROM temporal_price_points"
                ).fetchone()[0]),
                "outcome_rows": outcome_rows_total,
                "v001_parity_status": v001_parity["status"],
                "no_action_identity_status": no_action["status"],
                "action_reconciliation_status": action_audit["status"],
                "training_gate_status": gate_status,
            })
            if not stable:
                hard_gate_pass = False
                manifest["training_gate_status"] = "BLOCKED_INPUTS_CHANGED_DURING_BUILD"
                dest.execute(
                    "UPDATE training_gate SET status='BLOCKED_INPUTS_CHANGED_DURING_BUILD',"
                    "reason='Source, Core or Temporal V001 changed during materialization.' "
                    "WHERE gate_name='temporal_v002_training'"
                )
            for key, value in (
                ("manifest", manifest), ("v001_parity", v001_parity),
                ("no_action_identity", no_action),
                ("action_reconciliation", action_audit),
            ):
                dest.execute("INSERT INTO metadata VALUES(?,?)", (key, canonical(value)))
            dest.executescript("""
              CREATE INDEX idx_v002_origin_asset_day
                ON temporal_origins(asset_id,origin_trading_day);
              CREATE INDEX idx_v002_outcome_tau_status
                ON temporal_outcomes(tau_sessions,total_return_label_status);
              CREATE INDEX idx_v002_outcome_tau_origin
                ON temporal_outcomes(tau_sessions,origin_id);
              CREATE INDEX idx_v002_step_status
                ON temporal_return_steps(step_status,asset_id,trading_day);
            """)
            dest.commit()
        preliminary_audit = audit_output(temp_db, v001_db, cfg, replay=False)
        if not hard_gate_pass or preliminary_audit["integrity_status"] != "PASS":
            failure_audit = preliminary_audit | {
                "integrity_status": "FAIL",
                "failures": sorted(set(preliminary_audit["failures"] + [
                    "V002_HARD_DATA_GATE_FAILED"
                ])),
            }
            _write_reports(
                report_dir, manifest=manifest, v001_parity=v001_parity,
                no_action=no_action, action=action_audit, coverage=coverage,
                audit=failure_audit,
            )
            raise BuildBlocked("Temporal Dataset V002 data gate failed")
        os.replace(temp_db, output_db)
        manifest = manifest | {
            "dataset_sha256": file_digest(output_db), "reused_existing": False,
        }
        final_audit = preliminary_audit | {
            "training_gate_status": manifest["training_gate_status"]
        }
        _write_reports(
            report_dir, manifest=manifest, v001_parity=v001_parity,
            no_action=no_action, action=action_audit, coverage=coverage,
            audit=final_audit,
        )
        return final_audit | {"reused_existing": False, "output_db": str(output_db)}
    finally:
        temp_db.unlink(missing_ok=True)


def build_plan(
    source_db: Path = DEFAULT_SOURCE, core_db: Path = DEFAULT_CORE,
    v001_db: Path = DEFAULT_V001, config_path: Path = DEFAULT_CONFIG,
    *, strategy: str | None = None, extra_taus: Sequence[int] = (),
) -> dict[str, Any]:
    cfg = load_contract(config_path)
    selected_strategy, taus = resolve_taus(cfg, strategy, extra_taus)
    before = (file_state(source_db), file_state(core_db), file_state(v001_db))
    with closing(ro_connect(source_db)) as source, closing(ro_connect(core_db)) as core, closing(
        ro_connect(v001_db)
    ) as v001:
        core_meta, v001_manifest, cutoff_day, v001_taus = validate_inputs(
            source, core, v001, cfg
        )
        origin_rows = int(v001.execute("SELECT COUNT(*) FROM temporal_origins").fetchone()[0])
        price_rows = int(v001.execute("SELECT COUNT(*) FROM temporal_price_points").fetchone()[0])
        assets = int(v001.execute(
            "SELECT COUNT(DISTINCT asset_id) FROM temporal_origins"
        ).fetchone()[0])
        action_counts = [dict(row) for row in source.execute(
            """WITH ranked AS (
              SELECT o.asset_id,o.effective_trading_day,o.action_type,v.is_present,
                     ROW_NUMBER() OVER(PARTITION BY o.asset_id,o.effective_trading_day,
                       o.action_type ORDER BY o.observation_sequence DESC,
                       julianday(o.observed_at) DESC,o.action_observation_id DESC) rn
              FROM corporate_action_observations o JOIN corporate_action_versions v
                USING(corporate_action_version_id)
              JOIN (SELECT DISTINCT asset_id FROM assets WHERE active=1
                    AND asset_type='equity') a USING(asset_id)
              WHERE o.effective_trading_day<=?
            ) SELECT action_type,COUNT(*) latest_present_rows
              FROM ranked WHERE rn=1 AND is_present=1 GROUP BY action_type ORDER BY action_type""",
            (cutoff_day,),
        )]
    after = (file_state(source_db), file_state(core_db), file_state(v001_db))
    return {
        "version": "market_temporal_v002_materialization_plan",
        "status": "READY" if before == after else "BLOCKED_INPUTS_CHANGED_DURING_PLAN",
        "strategy": selected_strategy, "materialized_taus": taus,
        "materialized_tau_count": len(taus), "origin_rows": origin_rows,
        "price_grid_rows": price_rows, "assets": assets,
        "estimated_outcome_rows": origin_rows * len(taus),
        "dense_all_estimated_outcome_rows": origin_rows * 252,
        "source_cutoff_day": cutoff_day, "latest_present_action_counts": action_counts,
        "temporal_v001_materialized_taus": v001_taus,
        "temporal_v001_build_fingerprint": v001_manifest["build_fingerprint"],
        "core_metadata": core_meta, "source_core_and_v001_opened_read_only": True,
        "source_core_and_v001_stable_during_plan": before == after,
        "economic_formula": cfg["economic_return_contract"]["one_session_gross_factor"],
        "split_factor_is_applied_to_return": False,
        "provider_adjusted_close_role": "audit_only_not_target",
        "training_authorized": False, "model_training_performed": False,
        "v009_loaded_or_modified": False,
    }


def require_training_authorized(output_db: Path = DEFAULT_OUTPUT) -> None:
    with closing(ro_connect(output_db)) as conn:
        row = conn.execute(
            "SELECT status,authorized,reason FROM training_gate "
            "WHERE gate_name='temporal_v002_training'"
        ).fetchone()
        if row is None or int(row["authorized"]) != 1:
            status = "MISSING" if row is None else row["status"]
            reason = "gate missing" if row is None else row["reason"]
            raise RuntimeError(f"temporal V002 training blocked: {status}: {reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("plan", "build", "audit"), default="plan")
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--core-db", type=Path, default=DEFAULT_CORE)
    parser.add_argument("--v001-db", type=Path, default=DEFAULT_V001)
    parser.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument(
        "--strategy", choices=("configured_sparse", "configured_plus", "dense_all")
    )
    parser.add_argument("--taus", help="Explicit comma/range spec; requires configured_plus")
    parser.add_argument("--force-rebuild", action="store_true")
    args = parser.parse_args()
    extra_taus = parse_tau_spec(args.taus)
    if args.stage == "plan":
        plan = build_plan(
            args.source_db, args.core_db, args.v001_db, args.config,
            strategy=args.strategy, extra_taus=extra_taus,
        )
        _atomic_text(args.report_dir / "plan.json", json.dumps(
            plan, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False
        ) + "\n")
        print(json.dumps(plan, indent=2, sort_keys=True))
        return
    cfg = load_contract(args.config)
    if args.stage == "audit":
        audit = audit_output(args.output_db, args.v001_db, cfg, replay=True)
        manifest = _read_output_manifest(args.output_db) | {
            "dataset_sha256": file_digest(args.output_db), "audit_replayed": True,
        }
        _write_reports(
            args.report_dir, manifest=manifest, v001_parity=audit["v001_parity"],
            no_action=audit["no_action_identity"], action=audit["action_reconciliation"],
            coverage=audit["coverage"], audit=audit,
        )
        print(json.dumps(audit, indent=2, sort_keys=True))
        if audit["integrity_status"] != "PASS":
            raise BuildBlocked("Temporal Dataset V002 audit failed")
        return
    result = materialize(
        args.source_db, args.core_db, args.v001_db, args.output_db,
        args.config, args.report_dir, strategy=args.strategy,
        extra_taus=extra_taus, force_rebuild=args.force_rebuild,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

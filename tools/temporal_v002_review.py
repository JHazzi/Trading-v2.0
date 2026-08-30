"""Review Temporal V002 economic targets without mutating research databases.

This stage is deliberately downstream of materialization and upstream of any
model.  It validates distribution identities, long-horizon support, arbitrary
tau reconstruction and the small set of economically material cash events.
Special-event decisions live in a separate, versioned JSON artifact; V002 is
never rewritten to make a review pass.
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
    from .temporal_dataset_v001 import canonical, file_digest, file_state, ro_connect
else:  # pragma: no cover - documented script entry point
    from temporal_dataset_v001 import canonical, file_digest, file_state, ro_connect


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "processed" / "market_temporal_v002.db"
DEFAULT_CONFIG = ROOT / "config" / "temporal_v002_review.json"
DEFAULT_REPORT_DIR = ROOT / "reports" / "market_temporal_v002_review"

REQUIRED_TABLES = {
    "metadata",
    "dataset_horizons",
    "temporal_price_points",
    "temporal_origins",
    "temporal_corporate_actions",
    "temporal_return_steps",
    "temporal_outcomes",
    "coverage_by_horizon",
    "coverage_by_sector",
    "coverage_by_year",
    "training_gate",
}


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


def _metadata(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        str(row["key"]): _decode(row["value_json"])
        for row in conn.execute("SELECT key,value_json FROM metadata")
    }


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            handle.write(text)
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _write_json(path: Path, payload: dict[str, Any]) -> None:
    _atomic_text(
        path,
        json.dumps(payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
        + "\n",
    )


def load_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != "market_temporal_v002_economic_review_v001":
        raise ValueError("unsupported_temporal_v002_review_version")
    expected = payload.get("expected_v002_contract") or {}
    if expected != {
        "dataset_contract": "market_temporal_horizon_conditioned_total_return_v002",
        "label_version": "market_temporal_total_shareholder_return_v002",
        "training_gate_prefix": "BLOCKED_",
    }:
        raise ValueError("unsupported_review_source_contract")
    thresholds = payload.get("special_cash_review") or {}
    moderate = float(thresholds.get("moderate_cash_to_previous_close", 0))
    critical = float(thresholds.get("critical_cash_to_previous_close", 0))
    if not 0 < moderate <= critical < 1:
        raise ValueError("invalid_special_cash_thresholds")
    tolerances = payload.get("mathematical_gates") or {}
    if not 0 < float(tolerances.get("return_absolute_tolerance", 0)) <= 1e-7:
        raise ValueError("invalid_return_identity_tolerance")
    if not 0 < float(tolerances.get("prefix_log_absolute_tolerance", 0)) <= 1e-7:
        raise ValueError("invalid_prefix_identity_tolerance")
    quantiles = list(map(float, payload.get("distribution_audit", {}).get("quantiles", [])))
    if not quantiles or quantiles != sorted(set(quantiles)) or any(
        not 0 <= value <= 1 for value in quantiles
    ):
        raise ValueError("invalid_distribution_quantiles")
    manual = payload.get("manual_decision_contract") or {}
    dispositions = set(manual.get("allowed_dispositions", []))
    if dispositions != {
        "validated_cash_and_share_entitlement",
        "quarantine_incomplete_entitlement",
    }:
        raise ValueError("invalid_manual_dispositions")
    guards = payload.get("guards") or {}
    required_false = (
        "training_authorized",
        "model_training_performed",
        "v002_mutation_allowed",
        "source_core_v001_mutation_allowed",
        "v009_loaded_or_modified",
    )
    if any(guards.get(key) is not False for key in required_false):
        raise ValueError("temporal_v002_review_guard_mismatch")
    return payload


def _quantile(sorted_values: Sequence[float], probability: float) -> float | None:
    if not sorted_values:
        return None
    if len(sorted_values) == 1:
        return float(sorted_values[0])
    position = probability * (len(sorted_values) - 1)
    lower = int(math.floor(position))
    upper = int(math.ceil(position))
    if lower == upper:
        return float(sorted_values[lower])
    weight = position - lower
    return float(sorted_values[lower] * (1.0 - weight) + sorted_values[upper] * weight)


def _sample_summary(values: Iterable[float], quantiles: Sequence[float]) -> dict[str, Any]:
    ordered = sorted(float(value) for value in values)
    return {
        "sample_rows": len(ordered),
        "quantiles": {
            f"q{probability:.4f}": _quantile(ordered, probability)
            for probability in quantiles
        },
    }


def derive_total_return_from_prefix(
    prefix_log_factors: Sequence[float], origin_session_index: int, tau_sessions: int
) -> float | None:
    """Return an arbitrary-tau outcome from an immutable daily log prefix."""
    if tau_sessions < 1 or tau_sessions > 252:
        raise ValueError("tau_outside_1_252")
    target_index = origin_session_index + tau_sessions
    if origin_session_index < 0 or target_index >= len(prefix_log_factors):
        return None
    value = 100.0 * math.expm1(
        float(prefix_log_factors[target_index])
        - float(prefix_log_factors[origin_session_index])
    )
    if not math.isfinite(value) or value <= -100.0:
        raise ValueError("invalid_on_demand_total_return")
    return value


def deterministic_sampled_tau(
    state_id: str,
    lower: int,
    upper: int,
    seed: int,
    excluded_taus: set[int],
) -> int:
    choices = [tau for tau in range(lower, upper + 1) if tau not in excluded_taus]
    if not choices:
        raise ValueError("tau_sampling_band_has_no_eligible_value")
    token = hashlib.sha256(
        f"{seed}|{state_id}|{lower}|{upper}".encode("utf-8")
    ).digest()
    return choices[int.from_bytes(token[:8], "big") % len(choices)]


def _validate_v002(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    if not REQUIRED_TABLES.issubset(_objects(conn, "table")):
        failures.append("V002_SCHEMA_MISSING_TABLES")
        return {"status": "FAIL", "failures": failures}
    metadata = _metadata(conn)
    manifest = metadata.get("manifest")
    if not isinstance(manifest, dict):
        failures.append("V002_MANIFEST_MISSING")
        manifest = {}
    expected = cfg["expected_v002_contract"]
    if manifest.get("dataset_contract") != expected["dataset_contract"]:
        failures.append("V002_DATASET_CONTRACT_MISMATCH")
    if manifest.get("label_version") != expected["label_version"]:
        failures.append("V002_LABEL_VERSION_MISMATCH")
    for key in (
        "v001_parity_status",
        "no_action_identity_status",
        "action_reconciliation_status",
    ):
        if manifest.get(key) != "PASS":
            failures.append(f"V002_{key.upper()}_NOT_PASS")
    gate = conn.execute(
        "SELECT status,authorized,reason FROM training_gate "
        "WHERE gate_name='temporal_v002_training'"
    ).fetchone()
    if gate is None:
        failures.append("V002_TRAINING_GATE_MISSING")
        gate_payload = None
    else:
        gate_payload = dict(gate)
        if int(gate["authorized"]) != 0:
            failures.append("V002_UNEXPECTED_TRAINING_AUTHORIZATION")
        if not str(gate["status"]).startswith(expected["training_gate_prefix"]):
            failures.append("V002_TRAINING_GATE_STATUS_MISMATCH")
    counts = dict(conn.execute(
        "SELECT COUNT(*) outcome_rows,COUNT(DISTINCT origin_id) outcome_origins,"
        "COUNT(DISTINCT tau_sessions) outcome_taus FROM temporal_outcomes"
    ).fetchone())
    origin_rows = int(conn.execute("SELECT COUNT(*) FROM temporal_origins").fetchone()[0])
    materialized_taus = [
        int(row[0]) for row in conn.execute(
            "SELECT tau_sessions FROM dataset_horizons WHERE materialized=1 ORDER BY 1"
        )
    ]
    if counts["outcome_rows"] != origin_rows * len(materialized_taus):
        failures.append("V002_OUTCOME_CARTESIAN_COUNT_MISMATCH")
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "manifest": manifest,
        "training_gate": gate_payload,
        "origin_rows": origin_rows,
        "outcome_rows": int(counts["outcome_rows"]),
        "materialized_taus": materialized_taus,
    }


def build_plan(
    db_path: Path = DEFAULT_DB, config_path: Path = DEFAULT_CONFIG
) -> dict[str, Any]:
    cfg = load_contract(config_path)
    before = file_state(db_path)
    with closing(ro_connect(db_path)) as conn:
        validation = _validate_v002(conn, cfg)
        moderate = float(cfg["special_cash_review"]["moderate_cash_to_previous_close"])
        critical = float(cfg["special_cash_review"]["critical_cash_to_previous_close"])
        special = dict(conn.execute(
            "SELECT COUNT(*) cash_steps,"
            "COALESCE(SUM(cash_distribution/provider_close_previous>=?),0) moderate_or_higher,"
            "COALESCE(SUM(cash_distribution/provider_close_previous>=?),0) critical_or_higher "
            "FROM temporal_return_steps WHERE cash_distribution>0 "
            "AND provider_close_previous>0",
            (moderate, critical),
        ).fetchone())
    stable = before == file_state(db_path)
    return {
        "version": "market_temporal_v002_economic_review_plan_v001",
        "status": "READY" if validation["status"] == "PASS" and stable else "BLOCKED",
        "v002_validation": validation,
        "special_cash_counts": special,
        "v002_file_state_stable": stable,
        "review_outputs": [
            "economic_action_review.json",
            "target_distribution_report.json",
            "support_report.json",
            "on_demand_tau_report.json",
            "audit.json",
            "special_action_decisions_template.json",
        ],
        "training_authorized": False,
        "model_training_performed": False,
        "v009_loaded_or_modified": False,
    }


def _action_lineage(conn: sqlite3.Connection) -> dict[tuple[int, str], list[dict[str, Any]]]:
    grouped: dict[tuple[int, str], list[dict[str, Any]]] = defaultdict(list)
    for row in conn.execute(
        "SELECT * FROM temporal_corporate_actions WHERE action_type IN "
        "('dividend','capital_gain') ORDER BY asset_id,effective_trading_day,action_type"
    ):
        item = dict(row)
        item["normalized_action"] = _decode(item.pop("normalized_action_json"))
        grouped[(int(row["asset_id"]), str(row["effective_trading_day"]))].append(item)
    return grouped


def _review_id(asset_id: int, trading_day: str, cash_distribution: float) -> str:
    return hashlib.sha256(
        f"{asset_id}|{trading_day}|{cash_distribution:.17g}".encode("utf-8")
    ).hexdigest()[:20]


def _economic_action_review(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
    dataset_sha256: str,
    config_sha256: str,
    decisions_path: Path | None,
) -> tuple[dict[str, Any], dict[str, Any]]:
    moderate = float(cfg["special_cash_review"]["moderate_cash_to_previous_close"])
    critical = float(cfg["special_cash_review"]["critical_cash_to_previous_close"])
    lineage = _action_lineage(conn)
    tickers = {
        int(row["asset_id"]): str(row["ticker"])
        for row in conn.execute(
            "SELECT asset_id,MIN(ticker) ticker FROM temporal_origins GROUP BY asset_id"
        )
    }
    flagged: list[dict[str, Any]] = []
    cash_steps = 0
    for row in conn.execute(
        "SELECT asset_id,trading_day,provider_close_previous,provider_close_current,"
        "cash_distribution,economic_gross_factor,provider_control_factor,"
        "adjusted_close_audit_factor,provider_reconciliation_error,action_class,step_status "
        "FROM temporal_return_steps WHERE cash_distribution>0 "
        "ORDER BY cash_distribution/provider_close_previous DESC"
    ):
        cash_steps += 1
        previous = float(row["provider_close_previous"])
        ratio = float(row["cash_distribution"]) / previous
        if ratio < moderate:
            continue
        asset_id = int(row["asset_id"])
        day = str(row["trading_day"])
        economic = float(row["economic_gross_factor"])
        provider = float(row["provider_control_factor"])
        impact = dict(conn.execute(
            "SELECT COUNT(*) affected_outcomes,COUNT(DISTINCT o.origin_id) affected_origins "
            "FROM temporal_outcomes o JOIN temporal_origins s USING(origin_id) "
            "WHERE s.asset_id=? AND s.origin_session_index<"
            "(SELECT asset_session_index FROM temporal_return_steps "
            " WHERE asset_id=? AND trading_day=?) "
            "AND s.origin_session_index+o.tau_sessions>="
            "(SELECT asset_session_index FROM temporal_return_steps "
            " WHERE asset_id=? AND trading_day=?) "
            "AND o.total_return_label_status='usable'",
            (asset_id, asset_id, day, asset_id, day),
        ).fetchone())
        affected_outcomes = int(impact["affected_outcomes"])
        flagged.append({
            "review_id": _review_id(asset_id, day, float(row["cash_distribution"])),
            "severity": "critical" if ratio >= critical else "moderate",
            "asset_id": asset_id,
            "ticker": tickers.get(asset_id),
            "trading_day": day,
            "previous_close": previous,
            "current_close": float(row["provider_close_current"]),
            "cash_distribution": float(row["cash_distribution"]),
            "cash_to_previous_close": ratio,
            "economic_gross_factor": economic,
            "provider_control_factor": provider,
            "economic_vs_provider_absolute_gap": abs(economic - provider),
            "adjusted_close_audit_factor": float(row["adjusted_close_audit_factor"]),
            "provider_reconciliation_error": float(row["provider_reconciliation_error"]),
            "step_status": str(row["step_status"]),
            "affected_materialized_outcomes": affected_outcomes,
            "affected_origin_states": int(impact["affected_origins"]),
            "decision_required": affected_outcomes > 0,
            "lineage": lineage.get((asset_id, day), []),
        })
    decision_required_events = [item for item in flagged if item["decision_required"]]
    template = {
        "version": "market_temporal_v002_special_action_decisions_v001",
        "dataset_sha256": dataset_sha256,
        "review_config_sha256": config_sha256,
        "instructions": (
            "For every review_id, choose exactly one allowed disposition. Evidence must "
            "identify an authoritative transaction/distribution source or an auditable "
            "local lineage reference. This file never rewrites V002."
        ),
        "decisions": [
            {
                "review_id": item["review_id"],
                "ticker": item["ticker"],
                "trading_day": item["trading_day"],
                "disposition": None,
                "evidence": [],
                "rationale": None,
            }
            for item in decision_required_events
        ],
    }
    decision_failures: list[str] = []
    decision_map: dict[str, dict[str, Any]] = {}
    if decisions_path is not None:
        try:
            decisions = json.loads(decisions_path.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError) as exc:
            decision_failures.append(f"DECISION_FILE_UNREADABLE:{exc}")
            decisions = {}
        if decisions.get("version") != template["version"]:
            decision_failures.append("DECISION_VERSION_MISMATCH")
        if decisions.get("dataset_sha256") != dataset_sha256:
            decision_failures.append("DECISION_DATASET_SHA256_MISMATCH")
        if decisions.get("review_config_sha256") != config_sha256:
            decision_failures.append("DECISION_CONFIG_SHA256_MISMATCH")
        raw_decisions = decisions.get("decisions")
        if not isinstance(raw_decisions, list):
            decision_failures.append("DECISIONS_NOT_A_LIST")
            raw_decisions = []
        for item in raw_decisions:
            if not isinstance(item, dict) or not item.get("review_id"):
                decision_failures.append("INVALID_DECISION_ROW")
                continue
            review_id = str(item["review_id"])
            if review_id in decision_map:
                decision_failures.append(f"DUPLICATE_DECISION:{review_id}")
            decision_map[review_id] = item
    allowed = set(cfg["manual_decision_contract"]["allowed_dispositions"])
    required_ids = {item["review_id"] for item in decision_required_events}
    if decisions_path is not None:
        missing = required_ids - set(decision_map)
        extra = set(decision_map) - required_ids
        decision_failures.extend(f"MISSING_DECISION:{value}" for value in sorted(missing))
        decision_failures.extend(f"EXTRA_DECISION:{value}" for value in sorted(extra))
        for review_id in sorted(required_ids & set(decision_map)):
            item = decision_map[review_id]
            if item.get("disposition") not in allowed:
                decision_failures.append(f"INVALID_DISPOSITION:{review_id}")
            evidence = item.get("evidence")
            if not isinstance(evidence, list) or not evidence or any(
                not isinstance(value, str) or not value.strip() for value in evidence
            ):
                decision_failures.append(f"EVIDENCE_REQUIRED:{review_id}")
            rationale = item.get("rationale")
            if not isinstance(rationale, str) or len(rationale.strip()) < int(
                cfg["manual_decision_contract"]["minimum_rationale_characters"]
            ):
                decision_failures.append(f"RATIONALE_REQUIRED:{review_id}")
    dispositions = Counter(
        item.get("disposition") for item in decision_map.values()
        if item.get("disposition") in allowed
    )
    if not decision_required_events:
        status = "PASS"
    elif decisions_path is None:
        status = "REVIEW_REQUIRED"
    elif decision_failures:
        status = "FAIL_DECISION_CONTRACT"
    elif dispositions["quarantine_incomplete_entitlement"]:
        status = "PASS_WITH_VERSIONED_QUARANTINE"
    else:
        status = "PASS"
    report = {
        "version": "market_temporal_v002_economic_action_review_v001",
        "status": status,
        "cash_steps": cash_steps,
        "moderate_threshold": moderate,
        "critical_threshold": critical,
        "flagged_steps": len(flagged),
        "decision_required_steps": len(decision_required_events),
        "lineage_only_steps_outside_model_origin_support": (
            len(flagged) - len(decision_required_events)
        ),
        "critical_steps": sum(item["severity"] == "critical" for item in flagged),
        "moderate_steps": sum(item["severity"] == "moderate" for item in flagged),
        "flagged_events": flagged,
        "decision_file": None if decisions_path is None else str(decisions_path),
        "decision_failures": decision_failures,
        "disposition_counts": dict(sorted(dispositions.items())),
        "quarantined_review_ids": sorted(
            review_id for review_id, item in decision_map.items()
            if item.get("disposition") == "quarantine_incomplete_entitlement"
        ),
        "provider_control_interpretation": (
            "Adjusted-close reconciliation validates provider timing/units/share basis. "
            "It is not equality with the reinvested shareholder-wealth factor."
        ),
        "training_authorized": False,
    }
    return report, template


def _extreme_rows(conn: sqlite3.Connection, tau: int, direction: str, limit: int) -> list[dict[str, Any]]:
    order = "ASC" if direction == "lower" else "DESC"
    return [
        dict(row) for row in conn.execute(
            "SELECT s.state_id,s.asset_id,s.ticker,s.sector,s.origin_trading_day,"
            "o.target_trading_day,o.tau_sessions,o.raw_close_return_pct,o.total_return_pct,"
            "o.cash_distribution_count,o.split_action_count,o.action_overlap_class "
            "FROM temporal_outcomes o JOIN temporal_origins s USING(origin_id) "
            "WHERE o.tau_sessions=? AND o.total_return_label_status='usable' "
            f"ORDER BY o.total_return_pct {order},o.origin_id LIMIT ?",
            (tau, limit),
        )
    ]


def _target_distribution_report(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, Any]:
    quantiles = list(map(float, cfg["distribution_audit"]["quantiles"]))
    modulus = int(cfg["distribution_audit"]["quantile_sample_origin_modulus"])
    tolerance = float(cfg["mathematical_gates"]["return_absolute_tolerance"])
    audit_taus = set(map(int, cfg["distribution_audit"]["extreme_sample_taus"]))
    extreme_limit = int(cfg["distribution_audit"]["extreme_rows_per_tail"])
    horizons: list[dict[str, Any]] = []
    failures: list[str] = []
    taus = [
        int(row[0]) for row in conn.execute(
            "SELECT tau_sessions FROM dataset_horizons WHERE materialized=1 ORDER BY 1"
        )
    ]
    for tau in taus:
        aggregate = dict(conn.execute(
            "SELECT COUNT(*) total_rows,"
            "COALESCE(SUM(total_return_label_status='usable'),0) usable_rows,"
            "COALESCE(SUM(total_return_label_status='insufficient_future'),0) insufficient_rows,"
            "COALESCE(SUM(total_return_label_status='action_data_quarantine'),0) quarantine_rows,"
            "MIN(total_return_pct) minimum_total_return_pct,"
            "MAX(total_return_pct) maximum_total_return_pct,"
            "AVG(total_return_pct) mean_total_return_pct,"
            "MIN(raw_close_return_pct) minimum_raw_return_pct,"
            "MAX(raw_close_return_pct) maximum_raw_return_pct,"
            "AVG(raw_close_return_pct) mean_raw_return_pct,"
            "AVG(total_return_pct-raw_close_return_pct) mean_cash_uplift_pct,"
            "COALESCE(SUM(total_return_label_status='usable' AND "
            "(typeof(total_return_pct) NOT IN ('real','integer') OR "
            "ABS(total_return_pct)>1e308)),0) invalid_numeric_rows,"
            "COALESCE(SUM(total_return_label_status='usable' AND total_return_pct<=-100.0),0) "
            "lower_bound_violations,"
            "COALESCE(SUM(total_return_label_status='usable' AND "
            "total_return_pct+?<raw_close_return_pct),0) negative_cash_uplift_rows,"
            "COALESCE(SUM(total_return_label_status='usable' AND cash_distribution_count=0 "
            "AND ABS(total_return_pct-raw_close_return_pct)>?),0) no_cash_identity_mismatches,"
            "COALESCE(SUM(total_return_label_status='usable' AND cash_distribution_count>0 "
            "AND total_return_pct<=raw_close_return_pct),0) non_strict_cash_uplift_rows "
            "FROM temporal_outcomes WHERE tau_sessions=?",
            (tolerance, tolerance, tau),
        ).fetchone())
        sample_rows = list(conn.execute(
            "SELECT total_return_pct,total_return_pct-raw_close_return_pct uplift "
            "FROM temporal_outcomes WHERE tau_sessions=? "
            "AND total_return_label_status='usable' AND origin_id % ?=0",
            (tau, modulus),
        ))
        total_sample = _sample_summary(
            (float(row["total_return_pct"]) for row in sample_rows), quantiles
        )
        uplift_sample = _sample_summary(
            (float(row["uplift"]) for row in sample_rows), quantiles
        )
        hard_fields = (
            "quarantine_rows",
            "invalid_numeric_rows",
            "lower_bound_violations",
            "negative_cash_uplift_rows",
            "no_cash_identity_mismatches",
        )
        for field in hard_fields:
            if int(aggregate[field]) != 0:
                failures.append(f"TAU_{tau}_{field.upper()}:{aggregate[field]}")
        item = {
            "tau_sessions": tau,
            "exact_aggregates": aggregate,
            "deterministic_quantile_sample": {
                "origin_modulus": modulus,
                "total_return_pct": total_sample,
                "cash_uplift_pct": uplift_sample,
            },
        }
        if tau in audit_taus:
            item["extreme_rows"] = {
                "lower": _extreme_rows(conn, tau, "lower", extreme_limit),
                "upper": _extreme_rows(conn, tau, "upper", extreme_limit),
            }
        horizons.append(item)
    return {
        "version": "market_temporal_v002_target_distribution_review_v001",
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "quantiles_are_deterministic_sample_estimates": True,
        "exact_counts_means_bounds_and_identity_gates": True,
        "horizons": horizons,
        "training_authorized": False,
    }


def _support_report(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, Any]:
    horizon_rows = [dict(row) for row in conn.execute(
        "SELECT * FROM coverage_by_horizon ORDER BY tau_sessions"
    )]
    sector_rows = [dict(row) for row in conn.execute(
        "SELECT * FROM coverage_by_sector ORDER BY tau_sessions,sector"
    )]
    year_rows = [dict(row) for row in conn.execute(
        "SELECT * FROM coverage_by_year ORDER BY tau_sessions,origin_year"
    )]
    failures: list[str] = []
    for row in horizon_rows:
        if int(row["action_quarantine_origins"]) != 0:
            failures.append(f"H{row['tau_sessions']}_ACTION_QUARANTINE")
        if int(row["resolved_origins"]) != int(row["total_return_usable_origins"]):
            failures.append(f"H{row['tau_sessions']}_RESOLVED_NOT_FULLY_USABLE")
    for row in sector_rows:
        if int(row["action_quarantine_origins"]) != 0:
            failures.append(
                f"H{row['tau_sessions']}_SECTOR_{row['sector']}_ACTION_QUARANTINE"
            )
    for row in year_rows:
        if int(row["action_quarantine_origins"]) != 0:
            failures.append(
                f"H{row['tau_sessions']}_YEAR_{row['origin_year']}_ACTION_QUARANTINE"
            )
    common = dict(conn.execute(
        "SELECT COUNT(*) common_support_origins,MIN(origin_trading_day) first_origin_day,"
        "MAX(origin_trading_day) last_origin_day FROM temporal_origins s WHERE NOT EXISTS ("
        "SELECT 1 FROM temporal_outcomes o JOIN dataset_horizons h USING(tau_sessions) "
        "WHERE o.origin_id=s.origin_id AND h.materialized=1 "
        "AND o.total_return_label_status<>'usable')"
    ).fetchone())
    roles = []
    for row in conn.execute(
        "SELECT tau_sessions,role_json FROM dataset_horizons WHERE materialized=1 ORDER BY 1"
    ):
        roles.append({
            "tau_sessions": int(row["tau_sessions"]),
            "roles": _decode(row["role_json"]),
        })
    return {
        "version": "market_temporal_v002_support_review_v001",
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "coverage_by_horizon": horizon_rows,
        "coverage_by_sector": sector_rows,
        "coverage_by_origin_year": year_rows,
        "common_support_all_materialized_taus": common,
        "horizon_roles": roles,
        "primary_comparison_support_policy": (
            "Use common resolved origin support for cross-tau comparisons; report each "
            "tau's maximal resolved support separately as a secondary view."
        ),
        "training_authorized": False,
    }


def _on_demand_tau_report(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, Any]:
    contract = cfg["on_demand_tau_audit"]
    modulus = int(contract["origin_sample_modulus"])
    maximum = int(contract["maximum_sampled_origins"])
    seed = int(contract["seed"])
    tolerance = float(cfg["mathematical_gates"]["prefix_log_absolute_tolerance"])
    bands = [tuple(map(int, band)) for band in contract["sampling_bands_sessions"]]
    materialized = {
        int(row[0]) for row in conn.execute(
            "SELECT tau_sessions FROM dataset_horizons WHERE materialized=1"
        )
    }
    holdouts = set(map(int, contract["sealed_holdout_sessions"]))
    excluded = materialized | holdouts
    origins = [dict(row) for row in conn.execute(
        "SELECT origin_id,state_id,asset_id,origin_session_index,origin_trading_day "
        "FROM temporal_origins WHERE origin_id % ?=0 ORDER BY origin_id LIMIT ?",
        (modulus, maximum),
    )]
    by_asset: dict[int, list[dict[str, Any]]] = defaultdict(list)
    for origin in origins:
        by_asset[int(origin["asset_id"])].append(origin)
    generated = insufficient = direct_identity_failures = invalid = 0
    selected_counts: Counter[int] = Counter()
    maximum_log_error = 0.0
    examples: list[dict[str, Any]] = []
    for asset_id, asset_origins in sorted(by_asset.items()):
        steps = list(conn.execute(
            "SELECT asset_session_index,log_economic_gross_factor FROM temporal_return_steps "
            "WHERE asset_id=? ORDER BY asset_session_index",
            (asset_id,),
        ))
        if not steps:
            invalid += len(asset_origins) * len(bands)
            continue
        maximum_index = int(steps[-1]["asset_session_index"])
        prefix = [0.0] * (maximum_index + 1)
        logs = [0.0] * (maximum_index + 1)
        expected_index = 0
        for row in steps:
            index = int(row["asset_session_index"])
            if index != expected_index:
                invalid += 1
                expected_index = index
            if index:
                value = row["log_economic_gross_factor"]
                if value is None or not math.isfinite(float(value)):
                    invalid += 1
                    value = 0.0
                logs[index] = float(value)
                prefix[index] = prefix[index - 1] + logs[index]
            expected_index += 1
        for origin in asset_origins:
            origin_index = int(origin["origin_session_index"])
            for lower, upper in bands:
                tau = deterministic_sampled_tau(
                    str(origin["state_id"]), lower, upper, seed, excluded
                )
                selected_counts[tau] += 1
                value = derive_total_return_from_prefix(prefix, origin_index, tau)
                if value is None:
                    insufficient += 1
                    continue
                generated += 1
                target_index = origin_index + tau
                direct_log = math.fsum(logs[origin_index + 1:target_index + 1])
                prefix_log = prefix[target_index] - prefix[origin_index]
                error = abs(direct_log - prefix_log)
                maximum_log_error = max(maximum_log_error, error)
                if error > tolerance:
                    direct_identity_failures += 1
                if not math.isfinite(value) or value <= -100.0:
                    invalid += 1
                if len(examples) < 25:
                    examples.append({
                        "state_id": origin["state_id"],
                        "asset_id": asset_id,
                        "origin_trading_day": origin["origin_trading_day"],
                        "tau_sessions": tau,
                        "total_return_pct": value,
                    })
    failures = []
    if not origins:
        failures.append("NO_ORIGINS_SELECTED_FOR_ON_DEMAND_AUDIT")
    if not generated:
        failures.append("NO_ON_DEMAND_TARGETS_GENERATED")
    if direct_identity_failures:
        failures.append(f"PREFIX_DIRECT_LOG_MISMATCHES:{direct_identity_failures}")
    if invalid:
        failures.append(f"INVALID_ON_DEMAND_ROWS:{invalid}")
    if set(selected_counts) & excluded:
        failures.append("ON_DEMAND_SAMPLE_ENTERED_MATERIALIZED_OR_HOLDOUT_TAU")
    return {
        "version": "market_temporal_v002_on_demand_tau_review_v001",
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "sampled_origins": len(origins),
        "generated_targets": generated,
        "insufficient_future_targets": insufficient,
        "selected_tau_counts": {
            str(key): value for key, value in sorted(selected_counts.items())
        },
        "maximum_prefix_vs_direct_log_error": maximum_log_error,
        "prefix_log_absolute_tolerance": tolerance,
        "materialized_taus_excluded": sorted(materialized),
        "sealed_holdouts_excluded": sorted(holdouts),
        "examples": examples,
        "arbitrary_tau_contract": (
            "100 * expm1(prefix_log[target_index] - prefix_log[origin_index])"
        ),
        "training_authorized": False,
    }


def run_review(
    db_path: Path = DEFAULT_DB,
    config_path: Path = DEFAULT_CONFIG,
    report_dir: Path = DEFAULT_REPORT_DIR,
    decisions_path: Path | None = None,
) -> dict[str, Any]:
    cfg = load_contract(config_path)
    before = file_state(db_path)
    dataset_sha256 = file_digest(db_path)
    config_sha256 = file_digest(config_path)
    with closing(ro_connect(db_path)) as conn:
        validation = _validate_v002(conn, cfg)
        actions, template = _economic_action_review(
            conn, cfg, dataset_sha256, config_sha256, decisions_path
        )
        targets = _target_distribution_report(conn, cfg)
        support = _support_report(conn, cfg)
        on_demand = _on_demand_tau_report(conn, cfg)
    stable = before == file_state(db_path)
    mechanical_pass = (
        validation["status"] == "PASS"
        and targets["status"] == "PASS"
        and support["status"] == "PASS"
        and on_demand["status"] == "PASS"
        and stable
    )
    if not mechanical_pass or actions["status"].startswith("FAIL"):
        status = "FAIL"
    elif actions["status"] == "REVIEW_REQUIRED":
        status = "REVIEW_REQUIRED_SPECIAL_ACTIONS"
    elif actions["status"] == "PASS_WITH_VERSIONED_QUARANTINE":
        status = "PASS_WITH_VERSIONED_QUARANTINE"
    else:
        status = "PASS"
    audit = {
        "version": "market_temporal_v002_full_economic_review_v001",
        "status": status,
        "v002_db": str(db_path),
        "v002_dataset_sha256": dataset_sha256,
        "review_config_sha256": config_sha256,
        "v002_validation": validation,
        "economic_action_review_status": actions["status"],
        "target_distribution_status": targets["status"],
        "support_status": support["status"],
        "on_demand_tau_status": on_demand["status"],
        "v002_opened_read_only": True,
        "v002_stable_during_review": stable,
        "quarantined_review_ids": actions["quarantined_review_ids"],
        "downstream_selection_mask_required": bool(actions["quarantined_review_ids"]),
        "training_authorized": False,
        "model_training_performed": False,
        "v009_loaded_or_modified": False,
        "next_gate": (
            "SPECIAL_ACTION_DECISIONS_REQUIRED" if status == "REVIEW_REQUIRED_SPECIAL_ACTIONS"
            else "TEMPORAL_MODEL_PREREGISTRATION_ELIGIBLE" if status.startswith("PASS")
            else "REVIEW_FAILURES_BEFORE_CONTINUING"
        ),
    }
    _write_json(report_dir / "economic_action_review.json", actions)
    _write_json(report_dir / "target_distribution_report.json", targets)
    _write_json(report_dir / "support_report.json", support)
    _write_json(report_dir / "on_demand_tau_report.json", on_demand)
    _write_json(report_dir / "audit.json", audit)
    _write_json(report_dir / "special_action_decisions_template.json", template)
    summary = [
        "# Market Temporal V002 economic review", "",
        f"Overall status: `{status}`.",
        f"Special-action status: `{actions['status']}`.",
        f"Target distribution: `{targets['status']}`.",
        f"Support: `{support['status']}`.",
        f"Arbitrary tau: `{on_demand['status']}`.", "",
        "No model was trained and V009 was not loaded or modified.",
    ]
    _atomic_text(report_dir / "SUMMARY.md", "\n".join(summary) + "\n")
    return audit


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("plan", "audit"), default="plan")
    parser.add_argument("--v002-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument("--decisions", type=Path)
    args = parser.parse_args()
    if args.stage == "plan":
        result = build_plan(args.v002_db, args.config)
        _write_json(args.report_dir / "plan.json", result)
    else:
        result = run_review(
            args.v002_db, args.config, args.report_dir, args.decisions
        )
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))
    if result["status"] in {"BLOCKED", "FAIL"}:
        raise RuntimeError(f"Temporal V002 review did not pass: {result['status']}")


if __name__ == "__main__":
    main()

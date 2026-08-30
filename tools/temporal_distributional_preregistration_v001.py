"""Create the plan-only preregistration for Q(total_return | X, tau).

The command reads V002, Core and the economic-review reports.  It resolves
feature schema and exact purged fold counts, but contains no fit/predict path
and never reads holdout performance.  A PASS review makes runner
implementation eligible; it does not itself authorize ad-hoc training.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Sequence

if __package__:
    from .temporal_dataset_v001 import file_digest, file_state, ro_connect
else:  # pragma: no cover
    from temporal_dataset_v001 import file_digest, file_state, ro_connect


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_V002 = ROOT / "data" / "processed" / "market_temporal_v002.db"
DEFAULT_CORE = ROOT / "data" / "processed" / "market_daily_v003_core.db"
DEFAULT_REVIEW = ROOT / "reports" / "market_temporal_v002_review" / "audit.json"
DEFAULT_CONFIG = ROOT / "config" / "temporal_distributional_preregistration_v001.json"
DEFAULT_REPORT_DIR = ROOT / "reports" / "temporal_distributional_v001"


def _atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(
                payload, handle, indent=2, sort_keys=True,
                ensure_ascii=False, allow_nan=False,
            )
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def _decode(value: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return value


def _metadata(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        str(row["key"]): _decode(row["value_json"])
        for row in conn.execute("SELECT key,value_json FROM metadata")
    }


def load_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != "market_temporal_distributional_preregistration_v001":
        raise ValueError("unsupported_temporal_distributional_preregistration")
    dataset = payload.get("dataset") or {}
    if dataset.get("dataset_contract") != "market_temporal_horizon_conditioned_total_return_v002":
        raise ValueError("preregistration_dataset_contract_mismatch")
    if dataset.get("label_version") != "market_temporal_total_shareholder_return_v002":
        raise ValueError("preregistration_label_version_mismatch")
    horizon = payload.get("horizon_contract") or {}
    anchors = list(map(int, horizon.get("development_training_anchors", [])))
    holdouts = list(map(int, horizon.get("sealed_generalization_holdouts", [])))
    if set(anchors) & set(holdouts) or sorted(anchors + holdouts) != sorted(
        horizon.get("materialized_checkpoints", [])
    ):
        raise ValueError("invalid_preregistered_horizon_partition")
    if horizon.get("tau_domain") != {
        "minimum_sessions": 1,
        "maximum_sessions": 252,
        "integer_only": True,
        "unit": "eligible_asset_sessions",
    }:
        raise ValueError("invalid_preregistered_tau_domain")
    folds = payload.get("outer_evaluation") or {}
    if int(folds.get("folds", 0)) < 2 or not 0 < float(folds.get("initial_fraction", 0)) < 1:
        raise ValueError("invalid_outer_fold_contract")
    if folds.get("purge_rule") != "training_target_day_strictly_before_first_test_origin_day":
        raise ValueError("invalid_outer_purge_rule")
    guards = payload.get("guards") or {}
    for key in (
        "training_runner_implemented",
        "model_training_performed",
        "holdout_performance_read_during_plan",
        "v002_mutation_allowed",
        "core_mutation_allowed",
        "v009_loaded_or_modified",
    ):
        if guards.get(key) is not False:
            raise ValueError(f"preregistration_guard_mismatch:{key}")
    return payload


def _split_days(days: Sequence[str], initial_fraction: float, folds: int) -> list[tuple[int, int]]:
    initial = max(1, int(math.floor(len(days) * initial_fraction)))
    remaining = len(days) - initial
    if remaining < folds:
        raise ValueError("not_enough_origin_days_for_outer_folds")
    base, extra = divmod(remaining, folds)
    bounds = []
    start = initial
    for fold in range(folds):
        size = base + (1 if fold < extra else 0)
        bounds.append((start, start + size))
        start += size
    if start != len(days):
        raise AssertionError("outer_fold_partition_incomplete")
    return bounds


def _group_counts(
    conn: sqlite3.Connection,
    taus: Sequence[int],
    where_sql: str,
    parameters: Sequence[Any],
) -> dict[str, int]:
    placeholders = ",".join("?" for _ in taus)
    rows = conn.execute(
        "SELECT o.tau_sessions,COUNT(*) rows FROM temporal_outcomes o "
        "JOIN temporal_origins s USING(origin_id) "
        f"WHERE o.tau_sessions IN ({placeholders}) "
        "AND o.total_return_label_status='usable' AND " + where_sql
        + " GROUP BY o.tau_sessions ORDER BY o.tau_sessions",
        tuple(taus) + tuple(parameters),
    )
    result = {str(tau): 0 for tau in taus}
    result.update({str(int(row["tau_sessions"])): int(row["rows"]) for row in rows})
    return result


def _common_support_count(
    conn: sqlite3.Connection,
    taus: Sequence[int],
    first_day: str,
    last_day: str,
) -> int:
    placeholders = ",".join("?" for _ in taus)
    return int(conn.execute(
        "SELECT COUNT(*) FROM (SELECT o.origin_id FROM temporal_outcomes o "
        "JOIN temporal_origins s USING(origin_id) "
        f"WHERE o.tau_sessions IN ({placeholders}) "
        "AND s.origin_trading_day BETWEEN ? AND ? "
        "AND o.total_return_label_status='usable' GROUP BY o.origin_id "
        "HAVING COUNT(*)=?)",
        tuple(taus) + (first_day, last_day, len(taus)),
    ).fetchone()[0])


def _fold_plan(
    conn: sqlite3.Connection,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    evaluation = cfg["outer_evaluation"]
    anchors = list(map(int, cfg["horizon_contract"]["development_training_anchors"]))
    holdouts = list(map(int, cfg["horizon_contract"]["sealed_generalization_holdouts"]))
    all_days = [
        str(row[0]) for row in conn.execute(
            "SELECT DISTINCT origin_trading_day FROM temporal_origins ORDER BY 1"
        )
    ]
    maximum_anchor = max(anchors)
    days = [
        str(row[0]) for row in conn.execute(
            "SELECT DISTINCT s.origin_trading_day FROM temporal_outcomes o "
            "JOIN temporal_origins s USING(origin_id) WHERE o.tau_sessions=? "
            "AND o.total_return_label_status='usable' ORDER BY 1",
            (maximum_anchor,),
        )
    ]
    bounds = _split_days(days, float(evaluation["initial_fraction"]), int(evaluation["folds"]))
    folds: list[dict[str, Any]] = []
    failures: list[str] = []
    minimum_train = int(evaluation["minimum_train_rows_per_anchor"])
    minimum_test = int(evaluation["minimum_test_rows_per_anchor"])
    for number, (start, stop) in enumerate(bounds, 1):
        first_test, last_test = days[start], days[stop - 1]
        train_counts = _group_counts(
            conn, anchors,
            "s.origin_trading_day<? AND o.target_trading_day<?",
            (first_test, first_test),
        )
        test_counts = _group_counts(
            conn, anchors,
            "s.origin_trading_day BETWEEN ? AND ?",
            (first_test, last_test),
        )
        holdout_availability = _group_counts(
            conn, holdouts,
            "s.origin_trading_day BETWEEN ? AND ?",
            (first_test, last_test),
        )
        for tau, count in train_counts.items():
            if count < minimum_train:
                failures.append(f"FOLD_{number}_H{tau}_TRAIN_ROWS_{count}_BELOW_{minimum_train}")
        for tau, count in test_counts.items():
            if count < minimum_test:
                failures.append(f"FOLD_{number}_H{tau}_TEST_ROWS_{count}_BELOW_{minimum_test}")
        folds.append({
            "fold": number,
            "first_test_origin_day": first_test,
            "last_test_origin_day": last_test,
            "test_origin_day_count": stop - start,
            "training_rows_by_anchor_after_rowwise_target_end_purge": train_counts,
            "test_rows_by_development_anchor": test_counts,
            "sealed_holdout_availability_counts_only": holdout_availability,
            "development_common_support_origin_states": _common_support_count(
                conn, anchors, first_test, last_test
            ),
            "holdout_common_support_origin_states_counts_only": _common_support_count(
                conn, holdouts, first_test, last_test
            ),
        })
    if len(days) < int(evaluation["minimum_common_support_origin_days"]):
        failures.append(
            "COMMON_SUPPORT_ORIGIN_DAYS_"
            f"{len(days)}_BELOW_{evaluation['minimum_common_support_origin_days']}"
        )
    censored_tail = [day for day in all_days if day > days[-1]] if days else all_days
    return {
        "version": "market_temporal_distributional_outer_fold_plan_v001",
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "all_origin_days": len(all_days),
        "primary_common_support_origin_days": len(days),
        "first_primary_origin_day": days[0] if days else None,
        "last_primary_origin_day": days[-1] if days else None,
        "maximum_development_anchor_sessions": maximum_anchor,
        "right_censored_tail_origin_days": len(censored_tail),
        "first_right_censored_tail_day": censored_tail[0] if censored_tail else None,
        "last_right_censored_tail_day": censored_tail[-1] if censored_tail else None,
        "right_censored_tail_policy": (
            "Excluded from the cross-tau primary fold clock; retained for preregistered "
            "per-tau maximal-support recency diagnostics."
        ),
        "purge_rule": evaluation["purge_rule"],
        "folds": folds,
        "holdout_values_or_scores_read": False,
    }


def build_plan(
    v002_db: Path = DEFAULT_V002,
    core_db: Path = DEFAULT_CORE,
    review_path: Path = DEFAULT_REVIEW,
    config_path: Path = DEFAULT_CONFIG,
    report_dir: Path = DEFAULT_REPORT_DIR,
) -> dict[str, Any]:
    cfg = load_contract(config_path)
    before = (file_state(v002_db), file_state(core_db))
    blockers: list[str] = []
    try:
        review = json.loads(review_path.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        review = {}
        blockers.append("V002_ECONOMIC_REVIEW_MISSING_OR_UNREADABLE")
    review_status = review.get("status")
    if review_status not in {"PASS", "PASS_WITH_VERSIONED_QUARANTINE"}:
        blockers.append(f"V002_ECONOMIC_REVIEW_NOT_CLOSED:{review_status or 'MISSING'}")
    if review_status == "PASS_WITH_VERSIONED_QUARANTINE":
        blockers.append("VERSIONED_QUARANTINE_SELECTION_MASK_NOT_IMPLEMENTED")
    with closing(ro_connect(v002_db)) as v002, closing(ro_connect(core_db)) as core:
        metadata = _metadata(v002)
        manifest = metadata.get("manifest") or {}
        expected = cfg["dataset"]
        if manifest.get("dataset_contract") != expected["dataset_contract"]:
            blockers.append("V002_DATASET_CONTRACT_MISMATCH")
        if manifest.get("label_version") != expected["label_version"]:
            blockers.append("V002_LABEL_VERSION_MISMATCH")
        materialized = [
            int(row[0]) for row in v002.execute(
                "SELECT tau_sessions FROM dataset_horizons WHERE materialized=1 ORDER BY 1"
            )
        ]
        expected_taus = sorted(map(int, cfg["horizon_contract"]["materialized_checkpoints"]))
        if materialized != expected_taus:
            blockers.append("V002_MATERIALIZED_TAU_SET_MISMATCH")
        gate = v002.execute(
            "SELECT status,authorized FROM training_gate "
            "WHERE gate_name='temporal_v002_training'"
        ).fetchone()
        if gate is None or int(gate["authorized"]) != 0:
            blockers.append("V002_INTERNAL_BLOCK_GATE_MISSING")
        core_columns = {
            str(row[1]) for row in core.execute(
                "PRAGMA table_info(market_daily_v003_states)"
            )
        }
        required_features = set(cfg["model_contract"]["frozen_own_features"])
        missing_features = sorted(required_features - core_columns)
        if missing_features:
            blockers.append("CORE_FEATURES_MISSING:" + ",".join(missing_features))
        folds = _fold_plan(v002, cfg)
        if folds["status"] != "PASS":
            blockers.extend(folds["failures"])
        scale = {
            "origins": int(v002.execute("SELECT COUNT(*) FROM temporal_origins").fetchone()[0]),
            "assets": int(v002.execute(
                "SELECT COUNT(DISTINCT asset_id) FROM temporal_origins"
            ).fetchone()[0]),
            "materialized_outcomes": int(v002.execute(
                "SELECT COUNT(*) FROM temporal_outcomes"
            ).fetchone()[0]),
        }
    stable = before == (file_state(v002_db), file_state(core_db))
    if not stable:
        blockers.append("V002_OR_CORE_CHANGED_DURING_PLAN")
    status = "READY_FOR_RUNNER_IMPLEMENTATION_NO_TRAINING" if not blockers else "BLOCKED"
    plan = {
        "version": "market_temporal_distributional_preregistration_plan_v001",
        "status": status,
        "blockers": blockers,
        "config_sha256": file_digest(config_path),
        "v002_dataset_sha256": file_digest(v002_db),
        "core_file_state": file_state(core_db),
        "review_path": str(review_path),
        "review_status": review_status,
        "scale": scale,
        "resolved_feature_manifest": {
            "market_feature_version": cfg["dataset"]["market_feature_version"],
            "features": cfg["model_contract"]["frozen_own_features"],
            "tau_features": cfg["model_contract"]["tau_features"],
            "outcome_or_action_features_included": False,
        },
        "fold_plan_status": folds["status"],
        "v002_and_core_opened_read_only": True,
        "v002_and_core_stable_during_plan": stable,
        "holdout_values_or_performance_read": False,
        "training_runner_implemented": False,
        "training_authorized": False,
        "model_training_performed": False,
        "v009_loaded_or_modified": False,
        "next_gate": (
            "IMPLEMENT_FROZEN_RUNNER_AND_REAUDIT_BEFORE_ANY_FIT"
            if status.startswith("READY") else "CLOSE_ALL_BLOCKERS_WITHOUT_READING_PERFORMANCE"
        ),
    }
    _atomic_json(report_dir / "preregistration_plan.json", plan)
    _atomic_json(report_dir / "fold_plan.json", folds)
    _atomic_json(report_dir / "frozen_protocol.json", cfg)
    audit = {
        "version": "market_temporal_distributional_preregistration_audit_v001",
        "status": "PASS" if status.startswith("READY") else "BLOCKED",
        "plan_status": status,
        "blockers": blockers,
        "config_is_frozen_before_model_performance": True,
        "holdouts_are_counted_for_feasibility_only": True,
        "training_authorized": False,
        "model_training_performed": False,
        "v009_loaded_or_modified": False,
    }
    _atomic_json(report_dir / "audit.json", audit)
    return plan


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("plan",), default="plan")
    parser.add_argument("--v002-db", type=Path, default=DEFAULT_V002)
    parser.add_argument("--core-db", type=Path, default=DEFAULT_CORE)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()
    result = build_plan(
        args.v002_db, args.core_db, args.review, args.config, args.report_dir
    )
    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()

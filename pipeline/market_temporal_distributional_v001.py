"""Frozen, resumable runner for Q(total shareholder return | X, tau).

Development uses only the twelve declared anchors.  The five interpolation
holdouts are inaccessible until every development shard passes, all artifacts
are hash-frozen, and an opening marker is durably written.  Core, V002 and the
selection mask are attached read-only throughout.
"""
from __future__ import annotations

import argparse
from contextlib import closing
from dataclasses import dataclass
from datetime import datetime, timezone
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import sys
import tempfile
from typing import Any, Mapping, Sequence

import joblib
import numpy as np
import pandas as pd
from sklearn.ensemble import HistGradientBoostingRegressor


ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

from evaluation.market.temporal_distributional_v001 import (  # noqa: E402
    calibration_counts,
    development_gate,
    equal_tau_day_table,
    equal_tau_day_calibration,
    holdout_gate,
    mean_pinball,
    moving_block_bootstrap,
    pinball,
)
from models.market.temporal_distributional_v001 import (  # noqa: E402
    fit_to_target,
    monotone_rearrange,
    predict_bundle,
    target_to_fit,
    tau_coordinates,
)


DEFAULT_V002 = ROOT / "data" / "processed" / "market_temporal_v002.db"
DEFAULT_CORE = ROOT / "data" / "processed" / "market_daily_v003_core.db"
DEFAULT_CONFIG = ROOT / "config" / "temporal_distributional_runner_v001.json"
DEFAULT_FOLDS = ROOT / "reports" / "temporal_distributional_v001" / "fold_plan.json"
DEFAULT_PREREG = ROOT / "reports" / "temporal_distributional_v001" / "preregistration_plan.json"
DEFAULT_REVIEW = ROOT / "reports" / "market_temporal_v002_review" / "audit.json"
DEFAULT_TAIL = ROOT / "reports" / "market_temporal_v002_tail_audit_v001" / "audit.json"
DEFAULT_MASK_AUDIT = ROOT / "reports" / "market_temporal_v002_selection_mask_v001" / "audit.json"
DEFAULT_MASK_DB = ROOT / "reports" / "market_temporal_v002_selection_mask_v001" / "selection_mask.sqlite"
DEFAULT_OUTPUT = ROOT / "reports" / "temporal_distributional_runner_v001"


@dataclass
class Panel:
    origin_id: np.ndarray
    origin_day_index: np.ndarray
    origin_days: list[str]
    tau_sessions: np.ndarray
    actual_pct: np.ndarray
    own_state: np.ndarray


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def digest(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            value.update(block)
    return value.hexdigest()


def file_state(path: Path) -> tuple[int, int] | None:
    if not path.exists():
        return None
    stat = path.stat()
    return int(stat.st_size), int(stat.st_mtime_ns)


def atomic_json(path: Path, payload: Mapping[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_joblib(path: Path, value: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".joblib", dir=path.parent)
    os.close(fd)
    try:
        joblib.dump(value, temporary, compress=3)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def atomic_npz(path: Path, **arrays: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".npz", dir=path.parent)
    os.close(fd)
    try:
        np.savez_compressed(temporary, **arrays)
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def load_config(path: Path) -> dict[str, Any]:
    cfg = load_json(path)
    if cfg.get("version") != "market_temporal_distributional_runner_v001":
        raise ValueError("unsupported_temporal_runner_contract")
    anchors = list(map(int, cfg.get("development_anchors", [])))
    holdouts = list(map(int, cfg.get("sealed_holdouts", [])))
    if len(anchors) != 12 or len(holdouts) != 5 or set(anchors) & set(holdouts):
        raise ValueError("invalid_frozen_horizon_partition")
    panel = cfg.get("training_panel", {})
    offsets = list(map(int, panel.get("cyclic_offsets", [])))
    if panel.get("hash_algorithm") != "sha256" or offsets != [0, 4, 8]:
        raise ValueError("training_panel_selection_not_fully_frozen")
    if cfg["target_representation"].get("outcome_clipping") != "none":
        raise ValueError("target_clipping_is_forbidden")
    if int(cfg["execution"].get("maximum_tau_sessions", 0)) != 252:
        raise ValueError("tau_domain_changed")
    return cfg


def _gate_status(path: Path) -> str | None:
    return load_json(path).get("status") if path.exists() else None


def _source_snapshot(v002: Path, core: Path) -> dict[str, Any]:
    return {
        "v002": file_state(v002), "v002_journal": file_state(Path(str(v002) + "-journal")),
        "v002_wal": file_state(Path(str(v002) + "-wal")), "core": file_state(core),
        "core_journal": file_state(Path(str(core) + "-journal")), "core_wal": file_state(Path(str(core) + "-wal")),
    }


def _anchor_indices(state_id: str, anchor_count: int, offsets: Sequence[int]) -> set[int]:
    start = int.from_bytes(hashlib.sha256(state_id.encode("utf-8")).digest()[:8], "big") % int(anchor_count)
    return {(start + int(offset)) % int(anchor_count) for offset in offsets}


def _anchor_selected(state_id: str, tau: int, anchors: Sequence[int], offsets: Sequence[int]) -> int:
    try:
        index = anchors.index(int(tau))
    except ValueError:
        return 0
    return int(index in _anchor_indices(str(state_id), len(anchors), offsets))


def _ro_connection(v002: Path, core: Path, mask: Path, cfg: Mapping[str, Any]) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{v002.resolve()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("ATTACH DATABASE ? AS core", (f"file:{core.resolve()}?mode=ro&immutable=1",))
    conn.execute("ATTACH DATABASE ? AS mask", (f"file:{mask.resolve()}?mode=ro&immutable=1",))
    anchors = list(map(int, cfg["development_anchors"]))
    offsets = list(map(int, cfg["training_panel"]["cyclic_offsets"]))
    conn.create_function("frozen_anchor_selected", 2, lambda state, tau: _anchor_selected(str(state), int(tau), anchors, offsets), deterministic=True)
    conn.execute("PRAGMA query_only=ON")
    return conn


def runner_plan(
    v002: Path, core: Path, config_path: Path, fold_path: Path, prereg_path: Path,
    review_path: Path, tail_path: Path, mask_audit_path: Path, mask_db: Path, output: Path,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    failures: list[str] = []
    required = {
        "economic_review": (review_path, "PASS"), "tail_lineage_audit": (tail_path, "PASS"),
        "selection_mask": (mask_audit_path, "PASS"), "fold_plan": (fold_path, "PASS"),
        "preregistration": (prereg_path, "READY_FOR_RUNNER_IMPLEMENTATION_NO_TRAINING"),
    }
    statuses: dict[str, Any] = {}
    for name, (path, expected) in required.items():
        status = _gate_status(path)
        statuses[name] = {"path": str(path.relative_to(ROOT)), "status": status}
        if status != expected:
            failures.append(f"{name.upper()}_STATUS_{status}_EXPECTED_{expected}")
    before = _source_snapshot(v002, core)
    actual_v002_hash = digest(v002)
    if actual_v002_hash != cfg["expected_v002_sha256"]:
        failures.append("V002_HASH_DIFFERS_FROM_FROZEN_RUNNER_CONTRACT")
    if mask_audit_path.exists() and load_json(mask_audit_path).get("v002_sha256") != actual_v002_hash:
        failures.append("SELECTION_MASK_V002_HASH_MISMATCH")
    features = list(cfg["models"]["own_features"])
    schema_columns: list[str] = []
    anchor_counts = {str(tau): 0 for tau in cfg["development_anchors"]}
    states = 0
    if not failures:
        with closing(_ro_connection(v002, core, mask_db, cfg)) as conn:
            schema_columns = [str(row[1]) for row in conn.execute("PRAGMA core.table_info(market_daily_v003_states)")]
            missing = sorted(set(features) - set(schema_columns))
            if missing:
                failures.append("CORE_FEATURES_MISSING:" + ",".join(missing))
            anchors = list(map(int, cfg["development_anchors"]))
            offsets = list(map(int, cfg["training_panel"]["cyclic_offsets"]))
            for row in conn.execute("SELECT state_id FROM temporal_origins ORDER BY origin_id"):
                chosen = _anchor_indices(str(row[0]), len(anchors), offsets)
                for index in chosen:
                    anchor_counts[str(anchors[index])] += 1
                states += 1
            ratio = max(anchor_counts.values()) / min(anchor_counts.values())
            if ratio > float(cfg["training_panel"]["maximum_anchor_count_ratio"]):
                failures.append(f"TRAINING_ANCHOR_BALANCE_RATIO_{ratio:.6f}_EXCEEDS_CONTRACT")
    else:
        ratio = None
    after = _source_snapshot(v002, core)
    if before != after:
        failures.append("SOURCE_FILE_STATE_CHANGED_DURING_PLAN")
    result = {
        "version": cfg["version"], "stage": "plan", "status": "PASS" if not failures else "FAIL",
        "failures": failures, "generated_at": utc_now(), "gate_inputs": statuses,
        "config_sha256": digest(config_path), "runner_sha256": digest(Path(__file__)),
        "v002_sha256": actual_v002_hash, "core_file_state": file_state(core),
        "source_opened_read_only": True, "source_stable_during_plan": before == after,
        "v009_loaded_or_modified": False, "holdout_outcomes_or_performance_read": False,
        "development_training_authorized": not failures,
        "training_panel": {
            "unique_origins": states, "rows_before_rowwise_target_purge": int(sum(anchor_counts.values())),
            "selected_anchor_counts_before_purge": anchor_counts, "maximum_to_minimum_anchor_ratio": ratio,
            "selection_uses_outcomes": False,
        },
        "feature_manifest": features,
        "tau_domain": {"minimum_sessions": 1, "maximum_sessions": 252, "integer_only": True},
        "next_gate": "RUN_FIVE_DEVELOPMENT_FOLD_SHARDS" if not failures else "REPAIR_PREFLIGHT_BLOCKERS",
    }
    atomic_json(output / "plan.json", result)
    return result


def _require_plan(output: Path, config_path: Path) -> dict[str, Any]:
    path = output / "plan.json"
    if not path.exists():
        raise RuntimeError("runner_plan_missing_run_stage_plan")
    plan = load_json(path)
    if plan.get("status") != "PASS" or not plan.get("development_training_authorized"):
        raise RuntimeError("runner_plan_does_not_authorize_development")
    if plan.get("config_sha256") != digest(config_path):
        raise RuntimeError("runner_config_changed_after_plan")
    return plan


def _fold_definition(fold_path: Path, fold: int) -> dict[str, Any]:
    plan = load_json(fold_path)
    for item in plan["folds"]:
        if int(item["fold"]) == int(fold):
            return item
    raise ValueError(f"fold {fold} is outside the frozen plan")


def _panel_query(features: Sequence[str], taus: Sequence[int], mode: str) -> str:
    placeholders = ",".join("?" for _ in taus)
    columns = ",".join(f's."{name}"' for name in features)
    selection = "AND frozen_anchor_selected(t.state_id,o.tau_sessions)=1" if mode == "train" else ""
    day_clause = (
        "t.origin_trading_day<? AND o.target_trading_day<?" if mode == "train"
        else "t.origin_trading_day BETWEEN ? AND ?"
    )
    return (
        "SELECT o.origin_id,t.origin_trading_day,o.tau_sessions,o.total_return_pct," + columns + " "
        "FROM temporal_outcomes o JOIN temporal_origins t USING(origin_id) "
        "JOIN core.market_daily_v003_states s ON s.state_id=t.state_id "
        f"WHERE o.tau_sessions IN ({placeholders}) AND o.total_return_label_status='usable' "
        f"AND {day_clause} {selection} "
        "AND NOT EXISTS(SELECT 1 FROM mask.excluded_outcomes m WHERE m.origin_id=o.origin_id AND m.tau_sessions=o.tau_sessions) "
        "ORDER BY t.origin_trading_day,o.origin_id,o.tau_sessions"
    )


def load_panel(
    conn: sqlite3.Connection, features: Sequence[str], taus: Sequence[int], mode: str,
    first_day: str, last_day: str | None, chunk_rows: int,
) -> Panel:
    query = _panel_query(features, taus, mode)
    parameters: tuple[Any, ...] = tuple(map(int, taus)) + (
        (first_day, first_day) if mode == "train" else (first_day, str(last_day))
    )
    origins: list[np.ndarray] = []
    days_raw: list[np.ndarray] = []
    tau_parts: list[np.ndarray] = []
    target_parts: list[np.ndarray] = []
    feature_parts: list[np.ndarray] = []
    for frame in pd.read_sql_query(query, conn, params=parameters, chunksize=int(chunk_rows)):
        origins.append(frame["origin_id"].to_numpy(dtype="int64"))
        days_raw.append(frame["origin_trading_day"].astype(str).to_numpy())
        tau = frame["tau_sessions"].to_numpy(dtype="int16")
        tau_parts.append(tau)
        target_parts.append(frame["total_return_pct"].to_numpy(dtype="float64"))
        own = frame[list(features)].to_numpy(dtype="float32")
        feature_parts.append(own)
    if not origins:
        raise RuntimeError(f"empty_{mode}_panel")
    origin_id = np.concatenate(origins)
    day_text = np.concatenate(days_raw)
    tau = np.concatenate(tau_parts)
    actual = np.concatenate(target_parts)
    own_state = np.concatenate(feature_parts, axis=0)
    unique_days, day_index = np.unique(day_text, return_inverse=True)
    target_to_fit(actual)  # hard domain/finite gate; no clipping
    return Panel(origin_id, day_index.astype("int16"), unique_days.astype(str).tolist(), tau, actual, own_state)


def _design(panel: Panel) -> np.ndarray:
    return np.column_stack((panel.own_state, tau_coordinates(panel.tau_sessions))).astype("float32")


def _fit_models(X: np.ndarray, y_log: np.ndarray, cfg: Mapping[str, Any]) -> dict[str, Any]:
    profile = cfg["models"]["profile"]
    models: dict[str, Any] = {}
    for q in map(float, cfg["quantiles"]):
        model = HistGradientBoostingRegressor(
            loss="quantile", quantile=q, learning_rate=float(profile["learning_rate"]),
            max_iter=int(profile["max_iter"]), max_leaf_nodes=int(profile["max_leaf_nodes"]),
            min_samples_leaf=int(profile["min_samples_leaf"]),
            l2_regularization=float(profile["l2_regularization"]),
            early_stopping=bool(profile["early_stopping"]), random_state=int(profile["random_seed"]),
        )
        model.fit(X, y_log)
        models[str(q)] = model
    return models


def _predict_models(models: Mapping[str, Any], X: np.ndarray, quantiles: Sequence[float]) -> np.ndarray:
    log_prediction = np.column_stack([models[str(float(q))].predict(X) for q in quantiles])
    return monotone_rearrange(fit_to_target(log_prediction)).astype("float32")


def _simple_quantiles(train: Panel, quantiles: Sequence[float]) -> dict[str, list[float]]:
    y_log = target_to_fit(train.actual_pct)
    result: dict[str, list[float]] = {}
    for tau in sorted(set(map(int, train.tau_sessions))):
        values = y_log[train.tau_sessions == tau]
        result[str(tau)] = [float(x) for x in np.quantile(values, quantiles)]
    return result


def _predict_simple(simple: Mapping[str, Sequence[float]], tau: np.ndarray) -> np.ndarray:
    known_tau = np.asarray(sorted(map(int, simple)), dtype=float)
    known_x = np.log1p(known_tau)
    values = np.asarray([simple[str(int(value))] for value in known_tau], dtype=float)
    requested_x = np.log1p(np.asarray(tau, dtype=float))
    predicted_log = np.column_stack([
        np.interp(requested_x, known_x, values[:, column]) for column in range(values.shape[1])
    ])
    return monotone_rearrange(fit_to_target(predicted_log)).astype("float32")


def _placebo_design(train: Panel, design: np.ndarray, cfg: Mapping[str, Any], seed: int) -> tuple[np.ndarray, dict[str, Any]]:
    own_features = list(cfg["models"]["own_features"])
    preserved = set(cfg["models"]["preserved_placebo_features"])
    permuted_indices = [i for i, name in enumerate(own_features) if name not in preserved]
    preserved_indices = [i for i, name in enumerate(own_features) if name in preserved]
    unique_origin, first, inverse = np.unique(train.origin_id, return_index=True, return_inverse=True)
    origin_days = train.origin_day_index[first]
    base = train.own_state[first]
    donor = np.arange(len(unique_origin))
    rng = np.random.default_rng(int(seed))
    singleton = 0
    for day in np.unique(origin_days):
        positions = np.flatnonzero(origin_days == day)
        if len(positions) < 2:
            singleton += 1
            continue
        order = rng.permutation(positions)
        donor[order] = np.roll(order, 1)
    out = design.copy()
    for index in permuted_indices:
        out[:, index] = base[donor[inverse], index]
    if preserved_indices and not np.array_equal(out[:, preserved_indices], design[:, preserved_indices], equal_nan=True):
        raise RuntimeError("placebo_changed_preserved_volatility_features")
    if not np.array_equal(out[:, -3:], design[:, -3:], equal_nan=True):
        raise RuntimeError("placebo_changed_tau_coordinates")
    return out, {
        "seed": int(seed), "permutation_unit": "unique_origin_within_origin_day_then_shared_across_selected_taus",
        "unique_origins": int(len(unique_origin)), "singleton_origin_days": int(singleton),
        "permuted_features": [own_features[i] for i in permuted_indices],
        "preserved_features": sorted(preserved), "tau_coordinates_preserved": True,
    }


def _daily_records(table: pd.DataFrame) -> list[dict[str, Any]]:
    return [
        {"origin_trading_day": str(row.origin_trading_day), "delta_pct": float(row.loss_delta_baseline_minus_candidate)}
        for row in table.itertuples(index=False)
    ]


def _by_tau_records(panel: Panel, baseline: np.ndarray, candidate: np.ndarray) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for tau in sorted(set(map(int, panel.tau_sessions))):
        take = panel.tau_sessions == tau
        table = equal_tau_day_table(panel.origin_day_index[take], panel.tau_sessions[take], baseline[take], candidate[take], panel.origin_days)
        result[str(tau)] = _daily_records(table)
    return result


def _by_quantile_records(panel: Panel, baseline_pred: np.ndarray, candidate_pred: np.ndarray, quantiles: Sequence[float]) -> dict[str, list[dict[str, Any]]]:
    result: dict[str, list[dict[str, Any]]] = {}
    for index, q in enumerate(map(float, quantiles)):
        base = pinball(panel.actual_pct, baseline_pred[:, index], q)
        cand = pinball(panel.actual_pct, candidate_pred[:, index], q)
        table = equal_tau_day_table(panel.origin_day_index, panel.tau_sessions, base, cand, panel.origin_days)
        result[str(q)] = _daily_records(table)
    return result


def _fold_report(
    fold: int, phase: str, panel: Panel, candidate: np.ndarray, reference: np.ndarray,
    simple: np.ndarray, quantiles: Sequence[float], placebo: Mapping[str, np.ndarray] | None,
    diagnostics: Mapping[str, Any], artifact_paths: Mapping[str, Path], config_path: Path,
) -> dict[str, Any]:
    candidate_loss = mean_pinball(panel.actual_pct, candidate, quantiles)
    reference_loss = mean_pinball(panel.actual_pct, reference, quantiles)
    simple_loss = mean_pinball(panel.actual_pct, simple, quantiles)
    daily_ref = equal_tau_day_table(panel.origin_day_index, panel.tau_sessions, reference_loss, candidate_loss, panel.origin_days)
    daily_simple = equal_tau_day_table(panel.origin_day_index, panel.tau_sessions, simple_loss, candidate_loss, panel.origin_days)
    placebo_daily: dict[str, Any] = {}
    placebo_mean_records: list[dict[str, Any]] = []
    if placebo:
        losses = []
        for seed, predictions in placebo.items():
            loss = mean_pinball(panel.actual_pct, predictions, quantiles)
            losses.append(loss)
            placebo_daily[str(seed)] = _daily_records(equal_tau_day_table(
                panel.origin_day_index, panel.tau_sessions, loss, candidate_loss, panel.origin_days
            ))
        mean_loss = np.mean(np.column_stack(losses), axis=1)
        placebo_mean_records = _daily_records(equal_tau_day_table(
            panel.origin_day_index, panel.tau_sessions, mean_loss, candidate_loss, panel.origin_days
        ))
    return {
        "version": "market_temporal_distributional_fold_result_v001", "phase": phase,
        "fold": int(fold), "status": "PASS", "generated_at": utc_now(), "rows": int(len(panel.actual_pct)),
        "origin_days": len(panel.origin_days), "taus": sorted(set(map(int, panel.tau_sessions))),
        "config_sha256": digest(config_path), "target_clipping_applied": False,
        "candidate_calibration": calibration_counts(panel.actual_pct, candidate, quantiles),
        "reference_calibration": calibration_counts(panel.actual_pct, reference, quantiles),
        "daily_calibration": {
            "candidate": equal_tau_day_calibration(
                panel.origin_day_index, panel.tau_sessions, panel.actual_pct, candidate, quantiles, panel.origin_days
            ),
            "reference": equal_tau_day_calibration(
                panel.origin_day_index, panel.tau_sessions, panel.actual_pct, reference, quantiles, panel.origin_days
            ),
        },
        "daily": {
            "candidate_vs_reference": _daily_records(daily_ref),
            "candidate_vs_simple": _daily_records(daily_simple),
            "candidate_vs_mean_placebo": placebo_mean_records,
            "candidate_vs_each_placebo": placebo_daily,
        },
        "daily_by_tau_candidate_vs_reference": _by_tau_records(panel, reference_loss, candidate_loss),
        "daily_by_quantile_candidate_vs_reference": _by_quantile_records(panel, reference, candidate, quantiles),
        "point_delta_reference_minus_candidate_pct": float(daily_ref["loss_delta_baseline_minus_candidate"].mean()),
        "diagnostics": dict(diagnostics),
        "artifacts": {name: {"path": str(path.relative_to(ROOT)), "sha256": digest(path)} for name, path in artifact_paths.items()},
        "holdout_values_read": phase == "holdout",
    }


def _artifact_dir(output: Path, phase: str) -> Path:
    return output / "artifacts" / phase


def run_development_fold(
    fold: int, v002: Path, core: Path, mask_db: Path, config_path: Path,
    fold_path: Path, output: Path,
) -> dict[str, Any]:
    _require_plan(output, config_path)
    cfg = load_config(config_path)
    definition = _fold_definition(fold_path, fold)
    report_path = output / "development" / f"fold_{fold:02d}.json"
    if report_path.exists():
        existing = load_json(report_path)
        if existing.get("status") == "PASS" and existing.get("config_sha256") == digest(config_path):
            for value in existing.get("artifacts", {}).values():
                path = ROOT / value["path"]
                if not path.exists() or digest(path) != value["sha256"]:
                    raise RuntimeError("completed_fold_artifact_hash_mismatch")
            return {**existing, "idempotent_reuse": True}
        raise RuntimeError("development_fold_report_exists_but_is_not_reusable")
    before = _source_snapshot(v002, core)
    features = list(cfg["models"]["own_features"])
    anchors = list(map(int, cfg["development_anchors"]))
    with closing(_ro_connection(v002, core, mask_db, cfg)) as conn:
        train = load_panel(conn, features, anchors, "train", definition["first_test_origin_day"], None, cfg["execution"]["input_chunk_rows"])
        test = load_panel(conn, features, anchors, "test", definition["first_test_origin_day"], definition["last_test_origin_day"], cfg["execution"]["input_chunk_rows"])
    X_train = _design(train)
    X_test = _design(test)
    y_log = target_to_fit(train.actual_pct)
    quantiles = list(map(float, cfg["quantiles"]))
    candidate_models = _fit_models(X_train, y_log, cfg)
    candidate_pred = _predict_models(candidate_models, X_test, quantiles)
    vol63_index = features.index("asset_vol_63d_pct")
    reference_train = np.column_stack((train.own_state[:, vol63_index], X_train[:, -3:])).astype("float32")
    reference_test = np.column_stack((test.own_state[:, vol63_index], X_test[:, -3:])).astype("float32")
    reference_models = _fit_models(reference_train, y_log, cfg)
    reference_pred = _predict_models(reference_models, reference_test, quantiles)
    simple = _simple_quantiles(train, quantiles)
    simple_pred = _predict_simple(simple, test.tau_sessions)
    placebo_predictions: dict[str, np.ndarray] = {}
    placebo_diagnostics: dict[str, Any] = {}
    for seed in map(int, cfg["models"]["placebo_seeds"]):
        placebo_train, audit = _placebo_design(train, X_train, cfg, seed)
        models = _fit_models(placebo_train, y_log, cfg)
        placebo_predictions[str(seed)] = _predict_models(models, X_test, quantiles)
        placebo_diagnostics[str(seed)] = audit
        del placebo_train, models
    artifacts = _artifact_dir(output, "development")
    candidate_path = artifacts / f"fold_{fold:02d}_candidate.joblib"
    reference_path = artifacts / f"fold_{fold:02d}_reference.joblib"
    prediction_path = artifacts / f"fold_{fold:02d}_predictions.npz"
    candidate_bundle = {
        "version": "market_temporal_distributional_model_bundle_v001", "fold": int(fold),
        "quantiles": quantiles, "own_features": features, "models": candidate_models,
        "simple_train_only_log_quantiles": simple, "target_representation": cfg["target_representation"],
        "training_first_test_origin_day": definition["first_test_origin_day"], "config_sha256": digest(config_path),
    }
    reference_bundle = {
        "version": "market_temporal_distributional_model_bundle_v001", "fold": int(fold),
        "quantiles": quantiles, "own_features": ["asset_vol_63d_pct"], "models": reference_models,
        "target_representation": cfg["target_representation"], "config_sha256": digest(config_path),
    }
    atomic_joblib(candidate_path, candidate_bundle)
    atomic_joblib(reference_path, reference_bundle)
    atomic_npz(
        prediction_path, origin_id=test.origin_id, origin_day_index=test.origin_day_index,
        origin_days=np.asarray(test.origin_days), tau_sessions=test.tau_sessions,
        actual_total_return_pct=test.actual_pct.astype("float32"), candidate_quantiles_pct=candidate_pred,
        reference_quantiles_pct=reference_pred, simple_quantiles_pct=simple_pred,
        quantiles=np.asarray(quantiles, dtype="float32"),
    )
    report = _fold_report(
        fold, "development", test, candidate_pred, reference_pred, simple_pred, quantiles,
        placebo_predictions, {
            "train_rows": int(len(train.actual_pct)), "test_rows": int(len(test.actual_pct)),
            "train_unique_origins": int(len(np.unique(train.origin_id))),
            "selected_train_rows_by_tau": {str(t): int(np.count_nonzero(train.tau_sessions == t)) for t in anchors},
            "placebo_derangements": placebo_diagnostics,
        }, {"candidate_model": candidate_path, "reference_model": reference_path, "predictions": prediction_path}, config_path,
    )
    if before != _source_snapshot(v002, core):
        raise RuntimeError("source_file_state_changed_during_development_fold")
    report["source_stable_during_fit"] = True
    atomic_json(report_path, report)
    return report


def _records_values(records: Sequence[Mapping[str, Any]]) -> np.ndarray:
    ordered = sorted(records, key=lambda x: str(x["origin_trading_day"]))
    return np.asarray([float(x["delta_pct"]) for x in ordered], dtype=float)


def _bootstrap_set(values: np.ndarray, cfg: Mapping[str, Any]) -> dict[str, Any]:
    evaluation = cfg["evaluation"]
    return {
        str(block): moving_block_bootstrap(values, int(block), int(evaluation["bootstrap_repetitions"]), int(evaluation["bootstrap_seed"]))
        for block in evaluation["moving_block_lengths_origin_days"]
    }


def _combine_daily_calibration(
    folds: Sequence[Mapping[str, Any]], model: str, quantiles: Sequence[float]
) -> dict[str, Any]:
    per: dict[str, Any] = {}
    for q in map(float, quantiles):
        records = sum((fold["daily_calibration"][model][str(q)] for fold in folds), [])
        values = np.asarray([float(row["empirical_cdf"]) for row in records], dtype=float)
        empirical = float(np.mean(values))
        per[str(q)] = {
            "quantile": q, "origin_days": int(len(values)), "empirical_cdf": empirical,
            "calibration_error": float(empirical - q),
        }
    return {
        "weighting": "equal_tau_equal_origin_day_empirical_cdf", "per_quantile": per,
        "mean_absolute_quantile_calibration_error": float(np.mean([abs(x["calibration_error"]) for x in per.values()])),
    }


def aggregate_development(config_path: Path, output: Path) -> dict[str, Any]:
    cfg = load_config(config_path)
    folds = [load_json(output / "development" / f"fold_{fold:02d}.json") for fold in range(1, 6)]
    if any(x.get("status") != "PASS" or x.get("phase") != "development" for x in folds):
        raise RuntimeError("all_five_development_fold_shards_must_pass")
    reference_records = sum((x["daily"]["candidate_vs_reference"] for x in folds), [])
    ref_values = _records_values(reference_records)
    by_tau = {
        str(tau): _records_values(sum((x["daily_by_tau_candidate_vs_reference"][str(tau)] for x in folds), []))
        for tau in cfg["development_anchors"]
    }
    by_q = {
        str(float(q)): _records_values(sum((x["daily_by_quantile_candidate_vs_reference"][str(float(q))] for x in folds), []))
        for q in cfg["quantiles"]
    }
    placebo_mean_values = _records_values(sum((x["daily"]["candidate_vs_mean_placebo"] for x in folds), []))
    seeds = list(map(int, cfg["models"]["placebo_seeds"]))
    placebo_seed_values = {
        str(seed): _records_values(sum((x["daily"]["candidate_vs_each_placebo"][str(seed)] for x in folds), []))
        for seed in seeds
    }
    candidate_calibration = _combine_daily_calibration(folds, "candidate", cfg["quantiles"])
    reference_calibration = _combine_daily_calibration(folds, "reference", cfg["quantiles"])
    summary: dict[str, Any] = {
        "candidate_vs_reference": {"point_delta_pct": float(np.mean(ref_values)), "bootstrap": _bootstrap_set(ref_values, cfg)},
        "candidate_vs_mean_placebo": {"point_delta_pct": float(np.mean(placebo_mean_values)), "bootstrap": _bootstrap_set(placebo_mean_values, cfg)},
        "candidate_vs_each_placebo_point": {seed: float(np.mean(values)) for seed, values in placebo_seed_values.items()},
        "per_anchor_point_delta_pct": {tau: float(np.mean(values)) for tau, values in by_tau.items()},
        "per_quantile_point_delta_pct": {q: float(np.mean(values)) for q, values in by_q.items()},
        "positive_anchors": sum(float(np.mean(x)) > 0 for x in by_tau.values()),
        "positive_folds": sum(float(x["point_delta_reference_minus_candidate_pct"]) > 0 for x in folds),
        "improved_quantiles": sum(float(np.mean(x)) > 0 for x in by_q.values()),
        "candidate_calibration": candidate_calibration, "reference_calibration": reference_calibration,
        "candidate_calibration_mae": candidate_calibration["mean_absolute_quantile_calibration_error"],
        "reference_calibration_mae": reference_calibration["mean_absolute_quantile_calibration_error"],
    }
    gate = development_gate(summary, cfg["development_gate"])
    result = {
        "version": "market_temporal_distributional_development_summary_v001", "stage": "development_aggregate",
        "status": gate["status"], "generated_at": utc_now(), "config_sha256": digest(config_path),
        "folds": 5, "development_anchors": cfg["development_anchors"], "sealed_holdouts_read": False,
        "summary": summary, "gate": gate,
        "next_gate": "FREEZE_ALL_DEVELOPMENT_ARTIFACT_HASHES" if gate["status"].startswith("PASS_") else "DO_NOT_OPEN_HOLDOUTS_CLOSE_OR_REPORT_BRANCH",
    }
    atomic_json(output / "development_summary.json", result)
    return result


def freeze_development(config_path: Path, output: Path) -> dict[str, Any]:
    summary_path = output / "development_summary.json"
    summary = load_json(summary_path)
    if summary.get("status") != "PASS_DEVELOPMENTAL_REQUIRES_FRESH_HOLDOUT":
        raise RuntimeError("development_gate_did_not_authorize_freeze")
    paths = [
        config_path, Path(__file__), ROOT / "models" / "market" / "temporal_distributional_v001.py",
        ROOT / "evaluation" / "market" / "temporal_distributional_v001.py", output / "plan.json", summary_path,
        DEFAULT_PREREG, DEFAULT_FOLDS, DEFAULT_REVIEW, DEFAULT_TAIL, DEFAULT_MASK_AUDIT, DEFAULT_MASK_DB,
    ]
    for fold in range(1, 6):
        report = load_json(output / "development" / f"fold_{fold:02d}.json")
        paths.append(output / "development" / f"fold_{fold:02d}.json")
        paths.extend(ROOT / value["path"] for value in report["artifacts"].values())
    missing = [str(x) for x in paths if not x.exists()]
    if missing:
        raise RuntimeError("freeze_artifacts_missing:" + ",".join(missing))
    manifest = {str(path.relative_to(ROOT)): digest(path) for path in sorted(set(paths))}
    result = {
        "version": "market_temporal_distributional_development_freeze_v001", "status": "PASS",
        "frozen_at": utc_now(), "development_gate_status": summary["status"],
        "manifest": manifest, "manifest_sha256": hashlib.sha256(json.dumps(manifest, sort_keys=True, separators=(",", ":")).encode()).hexdigest(),
        "holdout_values_read_before_freeze": False, "model_refit_after_freeze_allowed": False,
        "next_gate": "OPEN_SEALED_HOLDOUTS_ONCE_WITH_FROZEN_MODELS",
    }
    atomic_json(output / "development_freeze.json", result)
    return result


def _verify_freeze(output: Path) -> tuple[dict[str, Any], str]:
    path = output / "development_freeze.json"
    freeze = load_json(path)
    if freeze.get("status") != "PASS":
        raise RuntimeError("development_freeze_not_pass")
    for relative, expected in freeze["manifest"].items():
        target = ROOT / relative
        if not target.exists() or digest(target) != expected:
            raise RuntimeError(f"frozen_artifact_changed:{relative}")
    return freeze, digest(path)


def _open_marker(output: Path, freeze_sha: str, fold: int) -> dict[str, Any]:
    path = output / "holdout_opening.json"
    if path.exists():
        marker = load_json(path)
        if marker.get("development_freeze_sha256") != freeze_sha:
            raise RuntimeError("holdout_marker_belongs_to_different_freeze")
    else:
        marker = {
            "version": "market_temporal_distributional_holdout_opening_v001", "status": "OPENING",
            "development_freeze_sha256": freeze_sha, "first_opened_at": utc_now(),
            "attempted_folds": [], "completed_folds": [], "values_opened_once": True,
        }
    if int(fold) not in marker["attempted_folds"]:
        marker["attempted_folds"].append(int(fold))
    marker["last_attempt_at"] = utc_now()
    atomic_json(path, marker)  # durable before any holdout target query
    return marker


def run_holdout_fold(
    fold: int, v002: Path, core: Path, mask_db: Path, config_path: Path,
    fold_path: Path, output: Path,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    freeze, freeze_sha = _verify_freeze(output)
    report_path = output / "holdout" / f"fold_{fold:02d}.json"
    if report_path.exists():
        existing = load_json(report_path)
        if existing.get("status") == "PASS" and existing.get("development_freeze_sha256") == freeze_sha:
            return {**existing, "idempotent_reuse": True}
        raise RuntimeError("holdout_fold_report_exists_but_is_not_reusable")
    marker = _open_marker(output, freeze_sha, fold)
    definition = _fold_definition(fold_path, fold)
    features = list(cfg["models"]["own_features"])
    holdouts = list(map(int, cfg["sealed_holdouts"]))
    before = _source_snapshot(v002, core)
    with closing(_ro_connection(v002, core, mask_db, cfg)) as conn:
        test = load_panel(conn, features, holdouts, "test", definition["first_test_origin_day"], definition["last_test_origin_day"], cfg["execution"]["input_chunk_rows"])
    artifacts = _artifact_dir(output, "development")
    candidate_bundle = joblib.load(artifacts / f"fold_{fold:02d}_candidate.joblib")
    reference_bundle = joblib.load(artifacts / f"fold_{fold:02d}_reference.joblib")
    candidate = predict_bundle(candidate_bundle, test.own_state, test.tau_sessions).astype("float32")
    vol63 = features.index("asset_vol_63d_pct")
    reference = predict_bundle(reference_bundle, test.own_state[:, [vol63]], test.tau_sessions).astype("float32")
    simple = _predict_simple(candidate_bundle["simple_train_only_log_quantiles"], test.tau_sessions)
    prediction_path = _artifact_dir(output, "holdout") / f"fold_{fold:02d}_predictions.npz"
    atomic_npz(
        prediction_path, origin_id=test.origin_id, origin_day_index=test.origin_day_index,
        origin_days=np.asarray(test.origin_days), tau_sessions=test.tau_sessions,
        actual_total_return_pct=test.actual_pct.astype("float32"), candidate_quantiles_pct=candidate,
        reference_quantiles_pct=reference, simple_quantiles_pct=simple,
        quantiles=np.asarray(cfg["quantiles"], dtype="float32"),
    )
    report = _fold_report(
        fold, "holdout", test, candidate, reference, simple, cfg["quantiles"], None,
        {"test_rows": int(len(test.actual_pct)), "model_refit_performed": False, "placebo_models_run": False},
        {"predictions": prediction_path}, config_path,
    )
    if before != _source_snapshot(v002, core):
        raise RuntimeError("source_file_state_changed_during_holdout_fold")
    report["development_freeze_sha256"] = freeze_sha
    report["frozen_manifest_sha256"] = freeze["manifest_sha256"]
    report["model_refit_performed"] = False
    atomic_json(report_path, report)
    marker = load_json(output / "holdout_opening.json")
    if int(fold) not in marker["completed_folds"]:
        marker["completed_folds"].append(int(fold))
    marker["completed_folds"].sort()
    marker["status"] = "OPENED_ALL_FOLDS" if marker["completed_folds"] == [1, 2, 3, 4, 5] else "OPENING"
    marker["last_completed_at"] = utc_now()
    atomic_json(output / "holdout_opening.json", marker)
    return report


def aggregate_holdout(config_path: Path, output: Path) -> dict[str, Any]:
    cfg = load_config(config_path)
    freeze, freeze_sha = _verify_freeze(output)
    marker = load_json(output / "holdout_opening.json")
    if marker.get("completed_folds") != [1, 2, 3, 4, 5]:
        raise RuntimeError("all_five_holdout_fold_shards_must_complete")
    folds = [load_json(output / "holdout" / f"fold_{fold:02d}.json") for fold in range(1, 6)]
    if any(x.get("development_freeze_sha256") != freeze_sha or x.get("model_refit_performed") for x in folds):
        raise RuntimeError("holdout_fold_is_not_bound_to_unchanged_freeze")
    records = sum((x["daily"]["candidate_vs_reference"] for x in folds), [])
    values = _records_values(records)
    by_tau = {
        str(tau): _records_values(sum((x["daily_by_tau_candidate_vs_reference"][str(tau)] for x in folds), []))
        for tau in cfg["sealed_holdouts"]
    }
    by_q = {
        str(float(q)): _records_values(sum((x["daily_by_quantile_candidate_vs_reference"][str(float(q))] for x in folds), []))
        for q in cfg["quantiles"]
    }
    candidate_calibration = _combine_daily_calibration(folds, "candidate", cfg["quantiles"])
    reference_calibration = _combine_daily_calibration(folds, "reference", cfg["quantiles"])
    summary: dict[str, Any] = {
        "candidate_vs_reference": {"point_delta_pct": float(np.mean(values)), "bootstrap": _bootstrap_set(values, cfg)},
        "per_holdout_horizon_point_delta_pct": {tau: float(np.mean(x)) for tau, x in by_tau.items()},
        "per_quantile_point_delta_pct": {q: float(np.mean(x)) for q, x in by_q.items()},
        "positive_holdout_horizons": sum(float(np.mean(x)) > 0 for x in by_tau.values()),
        "positive_folds": sum(float(x["point_delta_reference_minus_candidate_pct"]) > 0 for x in folds),
        "improved_quantiles": sum(float(np.mean(x)) > 0 for x in by_q.values()),
        "candidate_calibration": candidate_calibration, "reference_calibration": reference_calibration,
        "candidate_calibration_mae": candidate_calibration["mean_absolute_quantile_calibration_error"],
        "reference_calibration_mae": reference_calibration["mean_absolute_quantile_calibration_error"],
    }
    gate = holdout_gate(summary, cfg["holdout_gate"])
    result = {
        "version": "market_temporal_distributional_holdout_summary_v001", "stage": "holdout_aggregate",
        "status": gate["status"], "generated_at": utc_now(), "sealed_holdouts": cfg["sealed_holdouts"],
        "development_freeze_sha256": freeze_sha, "frozen_manifest_sha256": freeze["manifest_sha256"],
        "models_refit_after_freeze": False, "holdout_opened_once": True,
        "summary": summary, "gate": gate,
        "next_gate": "TEMPORAL_SECTION_COMPLETE_DOCUMENT_RESULT" if gate["status"].startswith("PASS_") else "TEMPORAL_SECTION_COMPLETE_CLOSE_BRANCH_WITHOUT_CONTINGENCY",
    }
    atomic_json(output / "holdout_summary.json", result)
    marker["status"] = "CLOSED_AFTER_AGGREGATION"
    marker["closed_at"] = utc_now()
    marker["holdout_summary_sha256"] = digest(output / "holdout_summary.json")
    atomic_json(output / "holdout_opening.json", marker)
    return result


def status_report(output: Path) -> dict[str, Any]:
    def status(path: Path) -> str:
        return str(load_json(path).get("status")) if path.exists() else "MISSING"
    folds_dev = {str(i): status(output / "development" / f"fold_{i:02d}.json") for i in range(1, 6)}
    marker_path = output / "holdout_opening.json"
    marker = load_json(marker_path) if marker_path.exists() else None
    # Deliberately do not inspect holdout result files before the durable marker exists.
    folds_holdout = (
        {str(i): status(output / "holdout" / f"fold_{i:02d}.json") for i in range(1, 6)}
        if marker is not None else {str(i): "SEALED" for i in range(1, 6)}
    )
    return {
        "version": "market_temporal_distributional_runner_status_v001", "plan": status(output / "plan.json"),
        "development_folds": folds_dev, "development_summary": status(output / "development_summary.json"),
        "development_freeze": status(output / "development_freeze.json"),
        "holdout_marker": marker.get("status") if marker else "SEALED_UNOPENED",
        "holdout_folds": folds_holdout, "holdout_summary": status(output / "holdout_summary.json") if marker else "SEALED",
    }


def console_payload(stage: str, result: Mapping[str, Any]) -> Mapping[str, Any]:
    """Keep fold terminals readable; complete evidence is persisted as JSON."""
    if stage not in {"develop-fold", "holdout-fold"}:
        return result
    diagnostics = result.get("diagnostics", {})
    return {
        "version": result.get("version"), "stage": stage, "phase": result.get("phase"),
        "fold": result.get("fold"), "status": result.get("status"), "rows": result.get("rows"),
        "train_rows": diagnostics.get("train_rows"),
        "point_delta_reference_minus_candidate_pct": result.get("point_delta_reference_minus_candidate_pct"),
        "artifacts": result.get("artifacts"), "idempotent_reuse": result.get("idempotent_reuse", False),
        "complete_report_persisted": True,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", required=True, choices=(
        "plan", "develop-fold", "develop-aggregate", "freeze", "holdout-fold", "holdout-aggregate", "status",
    ))
    parser.add_argument("--fold", type=int, choices=range(1, 6))
    parser.add_argument("--v002-db", type=Path, default=DEFAULT_V002)
    parser.add_argument("--core-db", type=Path, default=DEFAULT_CORE)
    parser.add_argument("--selection-mask", type=Path, default=DEFAULT_MASK_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--fold-plan", type=Path, default=DEFAULT_FOLDS)
    parser.add_argument("--preregistration", type=Path, default=DEFAULT_PREREG)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--tail-audit", type=Path, default=DEFAULT_TAIL)
    parser.add_argument("--mask-audit", type=Path, default=DEFAULT_MASK_AUDIT)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT)
    args = parser.parse_args()
    if args.stage in {"develop-fold", "holdout-fold"} and args.fold is None:
        parser.error("--fold is required for fold stages")
    if args.stage == "plan":
        result = runner_plan(args.v002_db, args.core_db, args.config, args.fold_plan, args.preregistration, args.review, args.tail_audit, args.mask_audit, args.selection_mask, args.output_dir)
    elif args.stage == "develop-fold":
        result = run_development_fold(args.fold, args.v002_db, args.core_db, args.selection_mask, args.config, args.fold_plan, args.output_dir)
    elif args.stage == "develop-aggregate":
        result = aggregate_development(args.config, args.output_dir)
    elif args.stage == "freeze":
        result = freeze_development(args.config, args.output_dir)
    elif args.stage == "holdout-fold":
        result = run_holdout_fold(args.fold, args.v002_db, args.core_db, args.selection_mask, args.config, args.fold_plan, args.output_dir)
    elif args.stage == "holdout-aggregate":
        result = aggregate_holdout(args.config, args.output_dir)
    else:
        result = status_report(args.output_dir)
    print(json.dumps(console_payload(args.stage, result), indent=2, sort_keys=True, allow_nan=False))
    if args.stage == "plan" and result.get("status") != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

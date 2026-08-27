from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

from evaluation.market.distributional_v009 import (
    evaluate_prospective,
    settle_available_outcomes,
)
from models.market.distributional_v009_prospective import (
    DEFAULT_ARTIFACT_DIR,
    DEFAULT_CONFIG,
    DEFAULT_CORE_DB,
    canonical_json,
    dataframe_sha256,
    file_sha256,
    fit_static_artifact,
    load_artifact,
    load_config,
    load_prediction_frame,
    load_training_frame,
    predict_static_distributions,
    save_artifact,
    sha256_json,
)
from storage.prospective_registry import (
    append_evaluation,
    canonical_json as registry_json,
    connect_registry,
    initialize_registry,
    register_experiment,
    register_fit,
    seal_prediction_batch,
    sha256_json as registry_sha,
    stable_id,
)

ROOT = Path(__file__).resolve().parents[1]
V0081_DIR = ROOT / "reports" / "market_brain_distributional_v0081" / "endogenous_closure_v001"
DEFAULT_V0081_SUMMARY = V0081_DIR / "benchmark_summary.json"
DEFAULT_V0081_H1 = V0081_DIR / "h1_benchmark.json"
DEFAULT_V0081_MANIFEST = V0081_DIR / "resolved_feature_manifest.json"
DEFAULT_V0081_CONFIG = ROOT / "config" / "market_brain_distributional_v0081.json"
DEFAULT_REGISTRY_DB = ROOT / "data" / "processed" / "market_brain_v009_prospective.db"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "market_brain_distributional_v009" / "prospective_holdout_v001"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def _write_immutable(path: Path, payload: Any) -> None:
    text = json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise RuntimeError(f"refusing to overwrite immutable V009 artifact: {path}")
        return
    path.write_text(text, encoding="utf-8")


def _read(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _source_audit(
    cfg: Mapping[str, Any],
    summary_path: Path,
    h1_path: Path,
    manifest_path: Path,
    source_config_path: Path,
) -> tuple[dict[str, Any], list[str]]:
    expected = {
        summary_path: str(cfg["source_v0081_summary_sha256"]),
        h1_path: str(cfg["source_v0081_h1_sha256"]),
        manifest_path: str(cfg["source_v0081_manifest_sha256"]),
        source_config_path: str(cfg["source_v0081_config_sha256"]),
    }
    problems: list[str] = []
    hashes: dict[str, str] = {}
    for path, wanted in expected.items():
        if not path.is_file():
            problems.append(f"missing source checkpoint {path}")
            continue
        hashes[str(path)] = file_sha256(path)
        if hashes[str(path)] != wanted:
            problems.append(f"source checkpoint hash changed: {path}")
    if problems:
        return {"file_sha256": hashes}, problems
    summary, h1, manifest = _read(summary_path), _read(h1_path), _read(manifest_path)
    gate = h1.get("horizon_gate", {})
    if summary.get("status") != "COMPLETE":
        problems.append("V008.1 summary is not complete")
    if summary.get("benchmark_version") != cfg["source_v0081_version"]:
        problems.append("V008.1 version mismatch")
    if gate.get("status") != "PASS_DEVELOPMENTAL_REQUIRES_FRESH_HOLDOUT":
        problems.append("V008.1 H1 developmental pass is absent")
    if not gate.get("checks") or not all(gate["checks"].values()):
        problems.append("V008.1 H1 did not retain every frozen gate")
    if list(manifest.get("own_state", [])) != list(cfg["frozen_own_features"]):
        problems.append("V008.1 feature manifest changed")
    return {"file_sha256": hashes, "h1_gate": gate, "manifest": manifest}, problems


def _universe(core_db: Path, cfg: Mapping[str, Any]) -> dict[str, Any]:
    with sqlite3.connect(core_db) as conn:
        rows = conn.execute(
            """
            SELECT asset_id,ticker,sector,state_id,state_time,state_point_in_time_verified
            FROM market_daily_v003_states
            WHERE feature_version=? AND trading_day=?
            ORDER BY asset_id
            """,
            (str(cfg["market_feature_version"]), str(cfg["universe_snapshot_day"])),
        ).fetchall()
    assets = [
        {
            "asset_id": int(row[0]),
            "ticker": str(row[1]),
            "sector": str(row[2] or "UNKNOWN"),
            "snapshot_state_id": str(row[3]),
            "snapshot_state_time": str(row[4]),
            "state_point_in_time_verified": int(row[5]),
        }
        for row in rows
    ]
    if len(assets) < int(cfg["minimum_predictions_per_origin"]):
        raise RuntimeError("universe snapshot does not meet V009 asset gate")
    if len({row["asset_id"] for row in assets}) != len(assets):
        raise RuntimeError("duplicate asset in V009 universe snapshot")
    return {
        "universe_contract": "fixed current-company Core V003 cohort on preregistered snapshot day",
        "snapshot_day": str(cfg["universe_snapshot_day"]),
        "market_feature_version": str(cfg["market_feature_version"]),
        "assets": assets,
        "asset_count": len(assets),
        "strict_historical_pit": False,
        "survivorship_free": False,
    }


def plan(
    config_path: Path,
    core_db: Path,
    summary_path: Path,
    h1_path: Path,
    manifest_path: Path,
    source_config_path: Path,
    output_dir: Path,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    source, problems = _source_audit(
        cfg, summary_path, h1_path, manifest_path, source_config_path
    )
    universe = None
    if not core_db.is_file():
        problems.append(f"missing Core V003 DB: {core_db}")
    else:
        try:
            universe = _universe(core_db, cfg)
        except Exception as exc:
            problems.append(str(exc))
    payload = {
        "status": "PASS" if not problems else "FAIL",
        "benchmark_version": cfg["version"],
        "model_version": cfg["model_version"],
        "reference_version": cfg["reference_version"],
        "registry_version": cfg["registry_version"],
        "frozen_question": (
            "On the first untouched prospective 252-session H1 block, does "
            "the one-time pre-holdout V008.1 own-state model improve "
            "origin-day-equal pinball loss over frozen raw vol63?"
        ),
        "source_checkpoint": source,
        "prospective_contract": {
            "not_before_origin_day": cfg["not_before_origin_day"],
            "fit_policy": cfg["fit_policy"],
            "refit_during_confirmatory_window": False,
            "training_target_clock_policy": cfg["training_target_clock_policy"],
            "cohort_policy": cfg["confirmatory_cohort_policy"],
            "confirmatory_origin_days": int(cfg["confirmatory_origin_days"]),
            "preliminary_descriptive_origin_days": int(cfg["preliminary_descriptive_origin_days"]),
            "maximum_seal_delay_hours": int(cfg["maximum_seal_delay_hours_from_state_time"]),
            "no_retroactive_prediction_backfill": True,
            "outcomes_separate_and_predictions_immutable": True,
        },
        "primary_gate": {
            "score": cfg["primary_score"],
            "bootstrap_block_length_origin_days": int(cfg["primary_bootstrap_block_length_origin_days"]),
            "minimum_positive_time_blocks": int(cfg["minimum_positive_time_blocks"]),
            "minimum_improved_quantiles": int(cfg["minimum_improved_quantiles"]),
            "require_candidate_calibration_not_worse": True,
        },
        "sample_size_rationale": {
            "V0081_H1_point_delta_pct": 0.004703166176309118,
            "V0081_H1_block10_ci95": [0.0029229998233487897, 0.0065821255020383634],
            "approximate_sessions_for_same_effect_to_clear_zero": 240,
            "chosen_sessions": 252,
        },
        "feature_manifest": source.get("manifest"),
        "feature_manifest_sha256": file_sha256(manifest_path) if manifest_path.is_file() else None,
        "universe_manifest_sha256": sha256_json(universe) if universe is not None else None,
        "strict_historical_pit": False,
        "current_cohort_not_survivorship_free": True,
        "claim_boundary": cfg["claim_boundary"],
        "missing": problems,
    }
    if universe is not None:
        _write_immutable(output_dir / "universe_manifest.json", universe)
    _write_immutable(output_dir / "preregistration.json", payload)
    return payload


def _frozen(output_dir: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    prereg = _read(output_dir / "preregistration.json")
    universe = _read(output_dir / "universe_manifest.json")
    if prereg.get("status") != "PASS":
        raise RuntimeError("V009 preregistration did not pass")
    return prereg, universe


def init_registry_stage(config_path: Path, registry_db: Path, output_dir: Path) -> dict[str, Any]:
    cfg = load_config(config_path)
    prereg, _ = _frozen(output_dir)
    migration = initialize_registry(registry_db)
    registration = register_experiment(
        registry_db,
        experiment_version=str(cfg["version"]),
        registry_version=str(cfg["registry_version"]),
        config_sha256=file_sha256(config_path),
        plan=prereg,
        source_checkpoint_sha256=str(cfg["source_v0081_summary_sha256"]),
        registered_at_utc=utc_now().isoformat(),
    )
    return {
        "status": "PASS",
        "migration": migration,
        "experiment_registration": registration,
        "registry_db": str(registry_db),
    }


def fit_stage(
    config_path: Path,
    core_db: Path,
    registry_db: Path,
    output_dir: Path,
    artifact_dir: Path,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    prereg, universe = _frozen(output_dir)
    with connect_registry(registry_db) as conn:
        existing = conn.execute(
            "SELECT * FROM prospective_model_fits WHERE experiment_version=?",
            (str(cfg["version"]),),
        ).fetchone()
    if existing is not None:
        return {
            "status": "ALREADY_FROZEN",
            "fit_id": str(existing["fit_id"]),
            "artifact_path": str(existing["artifact_path"]),
            "artifact_sha256": str(existing["artifact_sha256"]),
        }
    asset_ids = [int(row["asset_id"]) for row in universe["assets"]]
    training = load_training_frame(core_db, cfg, asset_ids)
    fitted_at = utc_now().isoformat()
    artifact = fit_static_artifact(
        training, cfg, str(prereg["feature_manifest_sha256"]), fitted_at
    )
    fit_id = stable_id(
        "fit",
        {
            "experiment_version": cfg["version"],
            "training_data_sha256": artifact["training_data_sha256"],
            "algorithm_contract_sha256": artifact["algorithm_contract_sha256"],
        },
    )
    artifact_path = artifact_dir / f"{fit_id}.joblib"
    artifact_sha = save_artifact(artifact, artifact_path)
    summary = artifact["training_summary"]
    fit_row = {
        "fit_id": fit_id,
        "experiment_version": str(cfg["version"]),
        "model_version": str(cfg["model_version"]),
        "fitted_at_utc": fitted_at,
        "horizon_sessions": int(cfg["horizon_sessions"]),
        "training_first_origin_day": summary["first_origin_day"],
        "training_last_origin_day": summary["last_origin_day"],
        "training_last_target_day": summary["last_target_day"],
        "training_rows": int(summary["rows"]),
        "training_origin_days": int(summary["origin_days"]),
        "training_assets": int(summary["assets"]),
        "training_data_sha256": artifact["training_data_sha256"],
        "feature_manifest_sha256": str(prereg["feature_manifest_sha256"]),
        "algorithm_contract_sha256": artifact["algorithm_contract_sha256"],
        "artifact_path": str(artifact_path.resolve()),
        "artifact_sha256": artifact_sha,
        "metadata_json": registry_json(
            {
                "fit_policy": cfg["fit_policy"],
                "refit_during_confirmatory_window": False,
                "training_target_cutoff_exclusive": cfg["not_before_origin_day"],
            }
        ),
    }
    registration = register_fit(registry_db, fit_row)
    return {"status": "FROZEN_PRE_HOLDOUT_FIT", "registration": registration, **fit_row}


def _existing_batch(registry_db: Path, version: str, origin_day: str) -> dict[str, Any] | None:
    with connect_registry(registry_db) as conn:
        row = conn.execute(
            """
            SELECT * FROM prospective_prediction_batches
            WHERE experiment_version=? AND origin_trading_day=?
            """,
            (version, origin_day),
        ).fetchone()
    return dict(row) if row is not None else None


def _assert_next_origin(
    core_db: Path,
    registry_db: Path,
    cfg: Mapping[str, Any],
    asset_ids: list[int],
    origin_day: str,
) -> None:
    with connect_registry(registry_db) as conn:
        value = conn.execute(
            "SELECT MAX(origin_trading_day) FROM prospective_prediction_batches WHERE experiment_version=?",
            (str(cfg["version"]),),
        ).fetchone()[0]
    previous = str(value) if value is not None else None
    if previous is None:
        return
    if origin_day <= previous:
        raise RuntimeError("new V009 origin must follow the last sealed origin")
    placeholders = ",".join("?" for _ in asset_ids)
    with sqlite3.connect(core_db) as conn:
        eligible = [
            str(row[0])
            for row in conn.execute(
                f"""
                SELECT trading_day FROM market_daily_v003_states
                WHERE feature_version=? AND trading_day>? AND trading_day<=?
                  AND asset_id IN ({placeholders})
                GROUP BY trading_day
                HAVING COUNT(DISTINCT asset_id)>=?
                ORDER BY trading_day
                """,
                [
                    str(cfg["market_feature_version"]),
                    previous,
                    origin_day,
                    *asset_ids,
                    int(cfg["minimum_predictions_per_origin"]),
                ],
            )
        ]
    if eligible != [origin_day]:
        raise RuntimeError(f"V009 cannot skip eligible origin sessions: found {eligible}")


def seal_stage(
    config_path: Path,
    core_db: Path,
    registry_db: Path,
    output_dir: Path,
    origin_day: str | None,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    prereg, universe = _frozen(output_dir)
    if origin_day is None:
        with sqlite3.connect(core_db) as conn:
            origin_day = str(
                conn.execute(
                    "SELECT MAX(trading_day) FROM market_daily_v003_states WHERE feature_version=?",
                    (str(cfg["market_feature_version"]),),
                ).fetchone()[0]
            )
    existing = _existing_batch(registry_db, str(cfg["version"]), str(origin_day))
    if existing is not None:
        return {"status": "ALREADY_SEALED", **existing}
    with connect_registry(registry_db) as conn:
        fit_raw = conn.execute(
            "SELECT * FROM prospective_model_fits WHERE experiment_version=?",
            (str(cfg["version"]),),
        ).fetchone()
    if fit_raw is None:
        raise RuntimeError("run V009 fit before sealing predictions")
    fit_row = dict(fit_raw)
    artifact_path = Path(str(fit_row["artifact_path"]))
    if file_sha256(artifact_path) != str(fit_row["artifact_sha256"]):
        raise RuntimeError("frozen V009 artifact hash changed")

    asset_ids = [int(row["asset_id"]) for row in universe["assets"]]
    _assert_next_origin(core_db, registry_db, cfg, asset_ids, str(origin_day))
    states = load_prediction_frame(core_db, cfg, asset_ids, str(origin_day))
    now = utc_now()
    state_time = datetime.fromisoformat(str(states["state_time"].iloc[0]))
    if state_time.tzinfo is None:
        raise RuntimeError("state_time must be timezone-aware")
    delay = (now - state_time.astimezone(timezone.utc)).total_seconds()
    maximum = int(cfg["maximum_seal_delay_hours_from_state_time"]) * 3600
    if delay < 0 or delay > maximum:
        raise RuntimeError(
            f"V009 seal clock outside frozen window: delay={delay:.0f}, maximum={maximum}"
        )

    artifact = load_artifact(
        artifact_path, cfg, str(prereg["feature_manifest_sha256"])
    )
    candidate, reference, diagnostics = predict_static_distributions(artifact, states)
    features = list(cfg["frozen_own_features"])
    state_hash = dataframe_sha256(
        states,
        [
            "state_id",
            "asset_id",
            "ticker",
            "trading_day",
            "state_time",
            "state_point_in_time_verified",
            *features,
        ],
    )
    batch_id = stable_id(
        "batch",
        {
            "experiment_version": cfg["version"],
            "fit_id": fit_row["fit_id"],
            "origin_trading_day": origin_day,
            "state_snapshot_sha256": state_hash,
        },
    )
    predictions: list[dict[str, Any]] = []
    role_specs = (
        (str(cfg["candidate_role"]), str(cfg["model_version"]), candidate),
        (str(cfg["reference_role"]), str(cfg["reference_version"]), reference),
    )
    for index, state in states.reset_index(drop=True).iterrows():
        snapshot = {
            name: float(state[name]) if state[name] == state[name] else None
            for name in features
        }
        feature_json = canonical_json(snapshot)
        feature_sha = hashlib.sha256(feature_json.encode()).hexdigest()
        for role, model_version, bundle in role_specs:
            payload = {
                "batch_id": batch_id,
                "model_role": role,
                "model_version": model_version,
                "asset_id": int(state["asset_id"]),
                "ticker": str(state["ticker"]),
                "state_id": str(state["state_id"]),
                "origin_trading_day": str(origin_day),
                "state_time": str(state["state_time"]),
                "state_point_in_time_verified": int(state["state_point_in_time_verified"]),
                "q05": float(bundle["quantiles"][0.05][index]),
                "q25": float(bundle["quantiles"][0.25][index]),
                "q50": float(bundle["quantiles"][0.50][index]),
                "q75": float(bundle["quantiles"][0.75][index]),
                "q95": float(bundle["quantiles"][0.95][index]),
                "probability_positive": float(bundle["probability_positive"][index]),
                "feature_snapshot_json": feature_json,
                "feature_snapshot_sha256": feature_sha,
            }
            payload["prediction_id"] = stable_id("prediction", payload)
            payload["payload_sha256"] = registry_sha(payload)
            predictions.append(payload)
    batch = {
        "batch_id": batch_id,
        "experiment_version": str(cfg["version"]),
        "fit_id": str(fit_row["fit_id"]),
        "origin_trading_day": str(origin_day),
        "state_time": str(states["state_time"].iloc[0]),
        "sealed_at_utc": now.isoformat(),
        "seal_delay_seconds": float(delay),
        "eligible_assets": int(len(universe["assets"])),
        "predicted_assets": int(len(states)),
        "state_snapshot_sha256": state_hash,
        "status": "SEALED",
        "metadata_json": registry_json(
            {
                "prediction_diagnostics": diagnostics,
                "feature_manifest_sha256": prereg["feature_manifest_sha256"],
                "actual_seal_clock": True,
            }
        ),
    }
    result = seal_prediction_batch(registry_db, batch, predictions)
    return {
        "status": result.upper(),
        "batch_id": batch_id,
        "origin_trading_day": origin_day,
        "predicted_assets": int(len(states)),
        "distribution_rows": int(len(predictions)),
        "seal_delay_seconds": float(delay),
        "prediction_diagnostics": diagnostics,
    }


def evaluate_stage(
    config_path: Path,
    core_db: Path,
    registry_db: Path,
    output_dir: Path,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    _, universe = _frozen(output_dir)
    asset_ids = [int(row["asset_id"]) for row in universe["assets"]]
    report, tables = evaluate_prospective(registry_db, core_db, cfg, asset_ids)
    report_hash = registry_sha(report)
    evaluation_id = stable_id(
        "evaluation",
        {
            "experiment_version": cfg["version"],
            "evaluation_version": cfg["evaluation_version"],
            "report_sha256": report_hash,
        },
    )
    row = {
        "evaluation_id": evaluation_id,
        "experiment_version": str(cfg["version"]),
        "evaluation_version": str(cfg["evaluation_version"]),
        "evaluated_at_utc": utc_now().isoformat(),
        "cohort_policy": str(cfg["confirmatory_cohort_policy"]),
        "first_origin_day": report.get("first_sealed_origin_day"),
        "last_origin_day": report.get("last_sealed_origin_day"),
        "origin_days": int(report.get("sealed_origin_days", 0)),
        "usable_rows": int(report.get("fixed_cohort_metrics", {}).get("rows", 0)),
        "status": str(report["status"]),
        "report_sha256": report_hash,
        "report_json": registry_json(report),
    }
    registration = append_evaluation(registry_db, row)
    report_path = output_dir / f"{evaluation_id}.json"
    _write_immutable(report_path, report)
    for name, table in tables.items():
        path = output_dir / f"{evaluation_id}_{name}.csv"
        if not path.exists():
            table.to_csv(path, index=False)
    return {
        **report,
        "evaluation_id": evaluation_id,
        "evaluation_registration": registration,
        "report_path": str(report_path),
    }


def status_stage(config_path: Path, registry_db: Path) -> dict[str, Any]:
    cfg = load_config(config_path)
    if not registry_db.is_file():
        return {"status": "REGISTRY_NOT_INITIALIZED"}
    with connect_registry(registry_db) as conn:
        experiments = conn.execute(
            "SELECT COUNT(*) FROM prospective_experiments WHERE experiment_version=?",
            (str(cfg["version"]),),
        ).fetchone()[0]
        fits = conn.execute(
            "SELECT COUNT(*) FROM prospective_model_fits WHERE experiment_version=?",
            (str(cfg["version"]),),
        ).fetchone()[0]
        batches = conn.execute(
            """
            SELECT COUNT(*),MIN(origin_trading_day),MAX(origin_trading_day)
            FROM prospective_prediction_batches WHERE experiment_version=?
            """,
            (str(cfg["version"]),),
        ).fetchone()
        outcomes = conn.execute(
            """
            SELECT COUNT(DISTINCT o.outcome_id)
            FROM prospective_prediction_outcomes o
            JOIN prospective_prediction_batches b ON b.batch_id=o.batch_id
            WHERE b.experiment_version=?
            """,
            (str(cfg["version"]),),
        ).fetchone()[0]
    return {
        "status": "PASS",
        "experiment_registered": bool(experiments),
        "frozen_fits": int(fits),
        "sealed_origin_days": int(batches[0]),
        "first_sealed_origin_day": batches[1],
        "last_sealed_origin_day": batches[2],
        "linked_outcomes": int(outcomes),
        "confirmatory_origin_days_required": int(cfg["confirmatory_origin_days"]),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        required=True,
        choices=("plan", "init-registry", "fit", "seal", "settle", "evaluate", "status"),
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--core-db", type=Path, default=DEFAULT_CORE_DB)
    parser.add_argument("--registry-db", type=Path, default=DEFAULT_REGISTRY_DB)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    parser.add_argument("--origin-day")
    parser.add_argument("--v0081-summary", type=Path, default=DEFAULT_V0081_SUMMARY)
    parser.add_argument("--v0081-h1", type=Path, default=DEFAULT_V0081_H1)
    parser.add_argument("--v0081-manifest", type=Path, default=DEFAULT_V0081_MANIFEST)
    parser.add_argument("--v0081-config", type=Path, default=DEFAULT_V0081_CONFIG)
    args = parser.parse_args()

    if args.stage == "plan":
        payload = plan(
            args.config,
            args.core_db,
            args.v0081_summary,
            args.v0081_h1,
            args.v0081_manifest,
            args.v0081_config,
            args.output_dir,
        )
    elif args.stage == "init-registry":
        payload = init_registry_stage(args.config, args.registry_db, args.output_dir)
    elif args.stage == "fit":
        payload = fit_stage(
            args.config,
            args.core_db,
            args.registry_db,
            args.output_dir,
            args.artifact_dir,
        )
    elif args.stage == "seal":
        payload = seal_stage(
            args.config,
            args.core_db,
            args.registry_db,
            args.output_dir,
            args.origin_day,
        )
    elif args.stage == "settle":
        payload = settle_available_outcomes(
            args.registry_db, args.core_db, load_config(args.config)
        )
    elif args.stage == "evaluate":
        payload = evaluate_stage(
            args.config, args.core_db, args.registry_db, args.output_dir
        )
    else:
        payload = status_stage(args.config, args.registry_db)
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.events.audit_v002 import DatasetGates, audit_frame
from evaluation.events.robustness_v0021 import (
    BOOTSTRAP_REPS,
    EARLY_OOS_INITIAL_FRACTION,
    OUTER_FOLDS,
    PRIMARY_BLOCK_LENGTH,
    PRIMARY_HORIZON,
    PRIMARY_INITIAL_FRACTION,
    RF_SEEDS,
    SIMPLE_FAMILIES,
    attach_accession_numbers,
    dependence_audit,
)
from models.events.robustness_v0021 import (
    rf_seed_experiment,
    simple_family_experiment,
    structural_experiment,
)
from models.events.train_v0031_deep import (
    DATASET_CONTRACT,
    DEFAULT_DB,
    EVENT_FEATURE_VERSION,
    LABEL_VERSION,
    MODEL_VERSION as DEEP_BASELINE_MODEL_VERSION,
    load_and_audit,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = ROOT / "reports" / "event_brain_v0021_robustness"
BASELINE_REPORT = (
    ROOT
    / "reports"
    / "event_brain_v0031_deep"
    / "event_brain_v002_h10_report.json"
)
ROBUSTNESS_MODEL_VERSION = "event_brain_v0021_robustness_h10_falsification"


def _write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def _load_frame(db: Path):
    if PRIMARY_HORIZON != 10:
        raise AssertionError("Primary robustness horizon changed")
    frame, audit = load_and_audit(
        db,
        PRIMARY_HORIZON,
        gates=DatasetGates(),
    )
    if audit["status"] != "PASS":
        raise RuntimeError(
            "Deep H10 dataset FAIL: "
            + json.dumps(audit, ensure_ascii=False)
        )
    frame = attach_accession_numbers(db, frame)
    return frame, audit


def _baseline_report_contract() -> dict[str, object]:
    if not BASELINE_REPORT.is_file():
        return {
            "status": "MISSING",
            "path": str(BASELINE_REPORT),
            "note": (
                "The robustness experiment can still run, but seed-42 "
                "reproduction cannot be cross-checked against the frozen "
                "deep report until that file exists."
            ),
        }

    report = json.loads(BASELINE_REPORT.read_text(encoding="utf-8"))
    failures = []
    if report.get("model_version") != DEEP_BASELINE_MODEL_VERSION:
        failures.append("model_version_mismatch")
    audit = report.get("dataset_audit", {})
    if audit.get("event_feature_version") != EVENT_FEATURE_VERSION:
        failures.append("event_feature_version_mismatch")
    if audit.get("label_version") != LABEL_VERSION:
        failures.append("label_version_mismatch")
    if int(report.get("horizon_sessions", -1)) != PRIMARY_HORIZON:
        failures.append("horizon_mismatch")

    primary = (
        report.get("comparisons", {})
        .get("capacity_control_vs_contextual_event", {})
    )
    return {
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "path": str(BASELINE_REPORT),
        "training_run_id": report.get("training_run_id"),
        "model_version": report.get("model_version"),
        "pooled_oos_rows": report.get("pooled_oos_rows"),
        "primary_mae_delta_pct": primary.get(
            "mae_delta_baseline_minus_candidate_pct"
        ),
        "primary_ci95": primary.get("mae_delta_ci95"),
    }


def stage_audit(db: Path) -> dict[str, object]:
    frame, audit = _load_frame(db)
    return {
        "status": "PASS",
        "experiment": ROBUSTNESS_MODEL_VERSION,
        "pre_registration": {
            "primary_horizon_sessions": PRIMARY_HORIZON,
            "primary_hypothesis": (
                "falsify whether H10 event information has reproducible "
                "incremental value over capacity control"
            ),
            "rf_seeds": list(RF_SEEDS),
            "simple_families": list(SIMPLE_FAMILIES),
            "outer_folds": OUTER_FOLDS,
            "primary_initial_fraction": PRIMARY_INITIAL_FRACTION,
            "early_oos_initial_fraction": EARLY_OOS_INITIAL_FRACTION,
            "bootstrap_reps": BOOTSTRAP_REPS,
            "primary_moving_block_length_origin_days": PRIMARY_BLOCK_LENGTH,
            "no_hyperparameter_tuning": True,
            "no_event_corpus_changes": True,
            "no_new_features": True,
        },
        "dataset_contract": {
            "event_feature_version": EVENT_FEATURE_VERSION,
            "label_version": LABEL_VERSION,
            "dataset_contract": DATASET_CONTRACT,
            "historical_reconstruction": True,
            "strict_pit": False,
        },
        "dataset_audit": audit,
        "dependence_audit": dependence_audit(frame),
        "baseline_report_contract": _baseline_report_contract(),
    }


def stage_rf_seeds(db: Path, report_dir: Path) -> dict[str, object]:
    frame, audit = _load_frame(db)
    result, oos_by_seed = rf_seed_experiment(frame)
    for seed, oos in oos_by_seed.items():
        out = report_dir / f"h10_rf_seed_{seed}_oos.csv"
        out.parent.mkdir(parents=True, exist_ok=True)
        oos.to_csv(out, index=False)
    payload = {
        "experiment": ROBUSTNESS_MODEL_VERSION,
        "stage": "rf-seeds",
        "dataset_audit": audit,
        "result": result,
    }
    _write_json(report_dir / "h10_rf_seeds.json", payload)
    return payload


def stage_simple_models(db: Path, report_dir: Path) -> dict[str, object]:
    frame, audit = _load_frame(db)
    payload = {
        "experiment": ROBUSTNESS_MODEL_VERSION,
        "stage": "simple-models",
        "dataset_audit": audit,
        "result": simple_family_experiment(frame),
    }
    _write_json(report_dir / "h10_simple_models.json", payload)
    return payload


def stage_structural(db: Path, report_dir: Path) -> dict[str, object]:
    frame, audit = _load_frame(db)
    payload = {
        "experiment": ROBUSTNESS_MODEL_VERSION,
        "stage": "structural",
        "dataset_audit": audit,
        "dependence_audit": dependence_audit(frame),
        "result": structural_experiment(frame),
    }
    _write_json(report_dir / "h10_structural.json", payload)
    return payload


def stage_summary(report_dir: Path) -> dict[str, object]:
    required = {
        "rf_seeds": report_dir / "h10_rf_seeds.json",
        "simple_models": report_dir / "h10_simple_models.json",
        "structural": report_dir / "h10_structural.json",
    }
    missing = [name for name, path in required.items() if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            "Faltan stages de robustness: " + ", ".join(missing)
        )
    payload = {
        "experiment": ROBUSTNESS_MODEL_VERSION,
        "stage": "summary",
        "interpretation_contract": {
            "objective": "falsification_not_optimization",
            "positive_delta_means_contextual_lower_mae": True,
            "no_single_p_value_is_a_promotion_gate": True,
            "do_not_select_best_seed": True,
            "do_not_select_best_model_family": True,
            "production_ready": False,
        },
        "rf_seeds": json.loads(
            required["rf_seeds"].read_text(encoding="utf-8")
        )["result"],
        "simple_models": json.loads(
            required["simple_models"].read_text(encoding="utf-8")
        )["result"],
        "structural": json.loads(
            required["structural"].read_text(encoding="utf-8")
        )["result"],
    }
    _write_json(report_dir / "h10_robustness_summary.json", payload)
    return payload


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--stage",
        required=True,
        choices=("audit", "rf-seeds", "simple-models", "structural", "summary", "all"),
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = p.parse_args()

    if args.stage == "audit":
        result = stage_audit(args.db)
    elif args.stage == "rf-seeds":
        result = stage_rf_seeds(args.db, args.report_dir)
    elif args.stage == "simple-models":
        result = stage_simple_models(args.db, args.report_dir)
    elif args.stage == "structural":
        result = stage_structural(args.db, args.report_dir)
    elif args.stage == "summary":
        result = stage_summary(args.report_dir)
    else:
        audit = stage_audit(args.db)
        if (
            audit["baseline_report_contract"]["status"] == "FAIL"
        ):
            raise RuntimeError("Frozen baseline report contract FAIL")
        result = {
            "audit": audit,
            "rf_seeds": stage_rf_seeds(args.db, args.report_dir),
            "simple_models": stage_simple_models(args.db, args.report_dir),
            "structural": stage_structural(args.db, args.report_dir),
            "summary": stage_summary(args.report_dir),
        }

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.events.audit_v002 import DatasetGates, audit_frame
from models.events.dataset_v002 import (
    MARKET_FEATURE_VERSION,
    load_dataset,
)
import models.events.train_v002 as frozen_v002

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
DEFAULT_ARTIFACT_DIR = (
    ROOT / "models" / "events" / "artifacts" / "deep_v0031"
)
DEFAULT_REPORT_DIR = ROOT / "reports" / "event_brain_v0031_deep"

EVENT_FEATURE_VERSION = "event_state_v0031_deep"
LABEL_VERSION = "event_reaction_daily_v0031_deep"
MODEL_VERSION = "event_brain_v002_architecture_on_deep_v0031"
DATASET_CONTRACT = "deep_sec_2016_2026_research_reconstruction_v0031"


def load_and_audit(
    db: Path,
    horizon_sessions: int,
    *,
    gates: DatasetGates = DatasetGates(),
):
    frame = load_dataset(
        db,
        horizon_sessions,
        event_feature_version=EVENT_FEATURE_VERSION,
        label_version=LABEL_VERSION,
    )
    audit = audit_frame(frame, gates=gates)
    audit["event_feature_version"] = EVENT_FEATURE_VERSION
    audit["label_version"] = LABEL_VERSION
    audit["market_feature_version"] = MARKET_FEATURE_VERSION
    audit["dataset_contract"] = DATASET_CONTRACT
    return frame, audit


def _install_deep_contract() -> None:
    """
    Keep the V0.2 training/evaluation implementation frozen while changing
    only the dataset version it receives.

    train_v002 imports audit_horizon and version constants into module globals,
    so a dedicated runner process can safely replace those bindings without
    modifying the frozen V0.2 source file or its model logic.
    """
    frozen_v002.audit_horizon = load_and_audit
    frozen_v002.EVENT_FEATURE_VERSION = EVENT_FEATURE_VERSION
    frozen_v002.LABEL_VERSION = LABEL_VERSION
    frozen_v002.MODEL_VERSION = MODEL_VERSION


def train_and_evaluate_deep(
    db: Path,
    horizon_sessions: int,
    *,
    seed: int = 42,
    outer_folds: int = 4,
    bootstrap_reps: int = 2000,
):
    _install_deep_contract()
    result = frozen_v002.train_and_evaluate(
        db,
        horizon_sessions,
        artifact_dir=DEFAULT_ARTIFACT_DIR,
        report_dir=DEFAULT_REPORT_DIR,
        seed=seed,
        outer_folds=outer_folds,
        bootstrap_reps=bootstrap_reps,
        gates=DatasetGates(),
    )

    # Assert after execution that registry/report metadata cannot silently point
    # back to the pilot dataset.
    assert result["model_version"] == MODEL_VERSION
    assert result["dataset_audit"]["event_feature_version"] == EVENT_FEATURE_VERSION
    assert result["dataset_audit"]["label_version"] == LABEL_VERSION
    result["deep_dataset_contract"] = {
        "event_feature_version": EVENT_FEATURE_VERSION,
        "label_version": LABEL_VERSION,
        "market_feature_version": MARKET_FEATURE_VERSION,
        "dataset_contract": DATASET_CONTRACT,
        "same_v002_training_code": True,
        "strict_pit_event_evidence": False,
    }
    return result


def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "Frozen Event Brain V0.2 code evaluated explicitly on Deep V003.1"
        )
    )
    p.add_argument("--horizon-sessions", type=int, required=True)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--outer-folds", type=int, default=4)
    p.add_argument("--bootstrap-reps", type=int, default=2000)
    args = p.parse_args()
    print(json.dumps(
        train_and_evaluate_deep(
            args.db,
            args.horizon_sessions,
            seed=args.seed,
            outer_folds=args.outer_folds,
            bootstrap_reps=args.bootstrap_reps,
        ),
        indent=2,
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()

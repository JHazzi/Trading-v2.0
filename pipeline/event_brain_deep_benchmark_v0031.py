from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.events.audit_v002 import DatasetGates
from models.events.train_v0031_deep import (
    DATASET_CONTRACT,
    DEFAULT_DB,
    EVENT_FEATURE_VERSION,
    LABEL_VERSION,
    MARKET_FEATURE_VERSION,
    MODEL_VERSION,
    load_and_audit,
    train_and_evaluate_deep,
)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=("audit", "benchmark"), required=True)
    p.add_argument("--horizons", default="1,3,5,10")
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--bootstrap-reps", type=int, default=2000)
    p.add_argument("--outer-folds", type=int, default=4)
    p.add_argument("--seed", type=int, default=42)
    args = p.parse_args()

    horizons = tuple(
        int(v.strip()) for v in args.horizons.split(",") if v.strip()
    )
    contract = {
        "model_version": MODEL_VERSION,
        "event_feature_version": EVENT_FEATURE_VERSION,
        "label_version": LABEL_VERSION,
        "market_feature_version": MARKET_FEATURE_VERSION,
        "dataset_contract": DATASET_CONTRACT,
        "same_v002_training_code": True,
        "horizons": list(horizons),
        "outer_folds": args.outer_folds,
        "bootstrap_reps": args.bootstrap_reps,
        "seed": args.seed,
    }

    if args.stage == "audit":
        audits = {}
        for horizon in horizons:
            _frame, audit = load_and_audit(
                args.db,
                horizon,
                gates=DatasetGates(),
            )
            audits[str(horizon)] = audit
        print(json.dumps(
            {"contract": contract, "audit": audits},
            indent=2,
            ensure_ascii=False,
        ))
        return

    results = {}
    for horizon in horizons:
        results[str(horizon)] = train_and_evaluate_deep(
            args.db,
            horizon,
            seed=args.seed,
            outer_folds=args.outer_folds,
            bootstrap_reps=args.bootstrap_reps,
        )
    print(json.dumps(
        {"contract": contract, "benchmark": results},
        indent=2,
        ensure_ascii=False,
    ))


if __name__ == "__main__":
    main()

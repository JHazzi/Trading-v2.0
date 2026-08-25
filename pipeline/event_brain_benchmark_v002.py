from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.events.audit_v002 import (
    DatasetGates,
    audit_horizon,
)
from models.events.train_v002 import (
    DEFAULT_DB,
    train_and_evaluate,
)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--stage",
        choices=("audit", "benchmark"),
        required=True,
    )
    p.add_argument(
        "--horizons",
        default="1,3,5,10",
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--bootstrap-reps", type=int, default=2000)
    p.add_argument("--outer-folds", type=int, default=4)
    args = p.parse_args()

    horizons = tuple(
        int(value.strip())
        for value in args.horizons.split(",")
        if value.strip()
    )

    if args.stage == "audit":
        result = {}
        for horizon in horizons:
            _frame, audit = audit_horizon(
                args.db,
                horizon,
                gates=DatasetGates(),
            )
            result[str(horizon)] = audit
        print(json.dumps(result, indent=2, ensure_ascii=False))
        return

    results = {}
    for horizon in horizons:
        results[str(horizon)] = train_and_evaluate(
            args.db,
            horizon,
            outer_folds=args.outer_folds,
            bootstrap_reps=args.bootstrap_reps,
        )
    print(json.dumps(results, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

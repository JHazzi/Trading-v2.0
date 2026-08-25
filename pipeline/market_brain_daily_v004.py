from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.market.daily_v003_benchmark_postmortem import (
    DEFAULT_BENCHMARK_DIR,
    DEFAULT_CORE_DB,
    DEFAULT_REPORT,
    run_postmortem,
)
from features.market.daily_v004_factorized_contract import CONTRACT


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--stage",
        required=True,
        choices=("contract", "postmortem"),
    )
    p.add_argument("--core-db", type=Path, default=DEFAULT_CORE_DB)
    p.add_argument(
        "--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK_DIR
    )
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--feature-sample-days", type=int, default=180)
    args = p.parse_args()

    if args.stage == "contract":
        result = {"status": "PASS", "contract": CONTRACT}
    else:
        result = run_postmortem(
            core_db=args.core_db,
            benchmark_dir=args.benchmark_dir,
            feature_sample_days=args.feature_sample_days,
        )
        args.report.parent.mkdir(parents=True, exist_ok=True)
        args.report.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

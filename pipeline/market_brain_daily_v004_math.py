from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.market.daily_v004_math_audit import audit, DEFAULT_REPORT
from features.market.daily_v004_math import (
    build,
    load_config,
    DEFAULT_CONFIG,
    DEFAULT_CORE_DB,
    DEFAULT_OUTPUT_DB,
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--stage", required=True, choices=("contract", "build", "audit"))
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--core-db", type=Path, default=DEFAULT_CORE_DB)
    p.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT_DB)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    a = p.parse_args()

    if a.stage == "contract":
        result = {"status": "PASS", "contract": load_config(a.config)}
    elif a.stage == "build":
        result = build(a.core_db, a.output_db, a.config)
    else:
        result = audit(a.output_db)
        a.report.parent.mkdir(parents=True, exist_ok=True)
        a.report.write_text(json.dumps(result, indent=2), encoding="utf-8")
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

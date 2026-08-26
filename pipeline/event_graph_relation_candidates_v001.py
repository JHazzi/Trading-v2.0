from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge.relations.relation_candidate_extraction_v001 import (
    DEFAULT_CONFIG,
    deterministic_qa_sample,
    extract,
    plan,
)
from evaluation.events.relation_candidate_extraction_audit_v001 import (
    audit,
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument(
        "--stage",
        required=True,
        choices=("plan", "extract", "audit", "qa-sample"),
    )
    a = p.parse_args()

    if a.stage == "plan":
        result = plan(a.config)
    elif a.stage == "extract":
        result = extract(a.config)
    elif a.stage == "audit":
        result = audit(a.config)
    else:
        result = deterministic_qa_sample(a.config)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

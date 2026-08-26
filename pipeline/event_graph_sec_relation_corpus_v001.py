from __future__ import annotations

import argparse
import json
from pathlib import Path

from knowledge.relations.sec_relation_corpus_v001 import (
    DEFAULT_CONFIG,
    build,
    plan,
)
from evaluation.events.sec_relation_corpus_audit_v001 import audit


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument(
        "--stage",
        required=True,
        choices=("plan", "build", "audit"),
    )
    a = p.parse_args()
    if a.stage == "plan":
        result = plan(a.config)
    elif a.stage == "build":
        result = build(a.config)
    else:
        result = audit(a.config)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

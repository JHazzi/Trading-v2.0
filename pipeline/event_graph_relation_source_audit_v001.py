from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.events.event_graph_relation_source_audit_v001 import (
    ROOT,
    audit,
)


DEFAULT_CONFIG = (
    ROOT / "config" / "event_graph_relation_source_audit_v001.json"
)


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument(
        "--report",
        type=Path,
        default=(
            ROOT / "reports"
            / "event_graph_relation_source_audit_v001.json"
        ),
    )
    a = p.parse_args()

    cfg = json.loads(a.config.read_text(encoding="utf-8"))
    if cfg["version"] != "event_graph_relation_source_audit_v001":
        raise ValueError("unexpected audit version")
    if cfg["read_only"] is not True:
        raise ValueError("source audit must remain read-only")
    result = audit(ROOT / cfg["database"])
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(
        json.dumps(result, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

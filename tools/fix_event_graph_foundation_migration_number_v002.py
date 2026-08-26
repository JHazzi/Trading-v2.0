from __future__ import annotations

import argparse
import hashlib
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

OLD_FILES = [
    ROOT / "database" / "migrations"
    / "019_event_graph_brain_foundation.sql",
    ROOT / "database" / "apply_migration_019.py",
]

EXPECTED_MARKERS = {
    "019_event_graph_brain_foundation.sql": [
        "event_graph_brain_foundation",
        "event_entity_links_v001",
        "temporal_relation_assertions_v001",
    ],
    "apply_migration_019.py": [
        'VERSION = "019"',
        'NAME = "event_graph_brain_foundation"',
    ],
}


def inspect() -> dict:
    out = {"status": "PASS", "old_files": {}, "failures": []}
    for path in OLD_FILES:
        if not path.exists():
            out["old_files"][path.name] = {"exists": False}
            continue
        text = path.read_text(encoding="utf-8")
        markers = EXPECTED_MARKERS[path.name]
        ours = all(m in text for m in markers)
        out["old_files"][path.name] = {
            "exists": True,
            "recognized_as_v001_foundation_file": ours,
            "sha256": hashlib.sha256(
                path.read_bytes()
            ).hexdigest(),
        }
        if not ours:
            out["status"] = "FAIL"
            out["failures"].append(
                f"refusing_to_remove_unrecognized_file:{path}"
            )
    return out


def apply() -> dict:
    out = inspect()
    if out["status"] != "PASS":
        return out
    removed = []
    for path in OLD_FILES:
        if path.exists():
            path.unlink()
            removed.append(str(path.relative_to(ROOT)))
    out["removed"] = removed
    return out


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = p.parse_args()
    print(json.dumps(apply() if a.apply else inspect(), indent=2))

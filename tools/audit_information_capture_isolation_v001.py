from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

V009_FILES = [
    "config/market_brain_daily_refresh_v009.json",
    "config/market_brain_distributional_v009.json",
    "evaluation/market/distributional_v009.py",
    "ingestion/prices/yahoo_daily_refresh_v009.py",
    "models/market/distributional_v009_prospective.py",
    "pipeline/market_brain_daily_refresh_v009.py",
    "pipeline/market_brain_distributional_v009.py",
]
FORBIDDEN_REFERENCES = [
    "information_capture_v001",
    "expectation_information_capture",
    "EXPECTATION_STATE_CONTRACTS",
]


def main() -> None:
    present = []
    missing = []
    violations = []
    for rel in V009_FILES:
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
            continue
        present.append(rel)
        text = p.read_text(encoding="utf-8", errors="replace")
        for needle in FORBIDDEN_REFERENCES:
            if needle in text:
                violations.append({"file": rel, "reference": needle})
    payload = {
        "status": "PASS" if not violations else "FAIL",
        "v009_files_present": present,
        "v009_files_missing": missing,
        "forbidden_cross_references": violations,
        "interpretation": "PASS means the capture foundation is not referenced by V009 code/config files.",
    }
    print(json.dumps(payload, indent=2, sort_keys=True))
    if violations:
        raise SystemExit(2)


if __name__ == "__main__":
    main()

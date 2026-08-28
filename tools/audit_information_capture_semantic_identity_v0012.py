from __future__ import annotations

import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

V009 = [
    "config/market_brain_daily_refresh_v009.json",
    "config/market_brain_distributional_v009.json",
    "evaluation/market/distributional_v009.py",
    "ingestion/prices/yahoo_daily_refresh_v009.py",
    "models/market/distributional_v009_prospective.py",
    "pipeline/market_brain_daily_refresh_v009.py",
    "pipeline/market_brain_distributional_v009.py",
]
TOKENS = [
    "information_capture_semantic_identity_v0012",
    "expectation_quality_v0012",
    "information_capture_scaling_v0012",
]


def main() -> None:
    missing = []
    forbidden = []
    for rel in V009:
        p = ROOT / rel
        if not p.exists():
            missing.append(rel)
            continue
        text = p.read_text(encoding="utf-8", errors="ignore")
        for token in TOKENS:
            if token in text:
                forbidden.append({"file": rel, "token": token})
    print(json.dumps({
        "status": "PASS" if not missing and not forbidden else "FAIL",
        "v009_files_missing": missing,
        "forbidden_cross_references": forbidden,
        "v009_interaction": "NONE",
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

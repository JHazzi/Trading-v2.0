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
FORBIDDEN = [
    "information_source_capture_v001",
    "alphavantage_expectations_v001",
    "provider_registry_v001",
    "information_capture_v001.db",
    "public_information_intake_v001",
    "public_information_catalog_v001.db",
    "public_information_v001",
    "financial_news_multisource",
    "public_information_semantics_audit_v001",
    "public_information_semantics_v001",
    "public_information_canonical_lake_v002",
    "public_information_v002_catalog.db",
    "public_information_v002",
]


def main() -> None:
    missing = []
    cross = []
    for rel in V009_FILES:
        path = ROOT / rel
        if not path.exists():
            missing.append(rel)
            continue
        text = path.read_text(encoding="utf-8", errors="ignore")
        for token in FORBIDDEN:
            if token in text:
                cross.append({"file": rel, "token": token})
    print(json.dumps({
        "status": "PASS" if not cross and not missing else "FAIL",
        "v009_files_missing": missing,
        "forbidden_cross_references": cross,
        "interpretation": "PASS means V009 remains blind to the new information-source acquisition branch."
    }, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

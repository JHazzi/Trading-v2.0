from __future__ import annotations

import argparse
import json
from pathlib import Path

from ingestion.expectations.alphavantage_expectations_v001 import (
    api_key_from_env,
    capture_calendar,
    capture_estimates_pilot,
)
from ingestion.expectations.foundation_v001 import audit_db
from research.information_sources.provider_audit_v001 import audit_registry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "information_source_capture_v001.json"
DEFAULT_REGISTRY = ROOT / "research" / "information_sources" / "provider_registry_v001.json"


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    parser = argparse.ArgumentParser(description="Prospective information-source capture. Model visibility remains blocked.")
    parser.add_argument("--stage", required=True, choices=["plan", "provider-audit", "capture-calendar", "capture-estimates-pilot", "audit"])
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--registry", default=str(DEFAULT_REGISTRY))
    args = parser.parse_args()

    cfg = load_json(Path(args.config))
    provider = cfg["providers"]["alpha_vantage"]
    db_path = ROOT / cfg["capture_db"]

    if args.stage == "plan":
        out = {
            "status": "READY",
            "contract_version": cfg["contract_version"],
            "capture_db": str(db_path),
            "feature_visibility": cfg["feature_visibility"],
            "alpha_vantage_enabled": bool(provider.get("enabled", False)),
            "pilot_tickers": provider.get("pilot_tickers", []),
            "recommended_first_live_capture": "EARNINGS_CALENDAR source snapshot, then EARNINGS_ESTIMATES pilot",
            "v009_interaction": "NONE; this pipeline does not import V009 and writes only information_capture_v001.db",
        }
    elif args.stage == "provider-audit":
        out = audit_registry(Path(args.registry))
    elif args.stage == "capture-calendar":
        if not provider.get("enabled", False):
            raise SystemExit("alpha_vantage.enabled=false; enable explicitly after provider/API-key review")
        key = api_key_from_env(provider.get("api_key_env", "ALPHAVANTAGE_API_KEY"))
        out = capture_calendar(
            db_path,
            key,
            horizon=provider.get("calendar_horizon", "3month"),
            timeout=int(provider.get("request_timeout_seconds", 45)),
        )
    elif args.stage == "capture-estimates-pilot":
        if not provider.get("enabled", False):
            raise SystemExit("alpha_vantage.enabled=false; enable explicitly after provider/API-key review")
        key = api_key_from_env(provider.get("api_key_env", "ALPHAVANTAGE_API_KEY"))
        out = capture_estimates_pilot(
            db_path,
            key,
            list(provider.get("pilot_tickers", [])),
            timeout=int(provider.get("request_timeout_seconds", 45)),
            min_interval=float(provider.get("minimum_seconds_between_symbol_requests", 13.0)),
        )
    else:
        out = audit_db(db_path)
        out["information_source_contract"] = cfg["contract_version"]
        out["feature_visibility"] = "BLOCKED"

    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

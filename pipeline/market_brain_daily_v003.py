from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.market.daily_v003_foundation_audit import audit_database
from features.market.daily_v003_contract import as_dict as contract_dict

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
DEFAULT_REPORT = (
    ROOT / "reports" / "market_brain_daily_v003" / "foundation_audit.json"
)


def stage_audit(db: Path, report: Path | None) -> dict[str, object]:
    result = audit_database(db)
    if report is not None:
        report.parent.mkdir(parents=True, exist_ok=True)
        report.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
    return result


def stage_contract() -> dict[str, object]:
    return {
        "status": "PASS",
        "contract": contract_dict(),
        "scientific_intent": {
            "market_brain_is_trained_on": "all_eligible_asset_days",
            "market_brain_is_not_conditioned_on": "event_occurrence",
            "state_clock": "session_close",
            "event_integration_later": (
                "latest_market_prediction_available_at_or_before_event_state"
            ),
            "macro_is_deferred_until_causal_vintage_contract": True,
            "external_proxies_are_not_required_for_foundation_audit": True,
        },
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--stage",
        required=True,
        choices=("contract", "audit"),
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument(
        "--no-write-report",
        action="store_true",
        help="Print only; do not write reports/market_brain_daily_v003/...",
    )
    args = p.parse_args()

    if args.stage == "contract":
        result = stage_contract()
    else:
        result = stage_audit(
            args.db,
            None if args.no_write_report else args.report,
        )

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

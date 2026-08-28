from __future__ import annotations

import argparse
import json
from pathlib import Path

from ingestion.expectations.alphavantage_expectations_v001 import api_key_from_env
from ingestion.expectations.alphavantage_scaling_v0011 import capture_selected, plan_due_symbols, read_symbols
from research.information_sources.expectation_quality_v0012 import quality_audit, revision_diff

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "information_capture_scaling_v0012.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Strict-PIT expectation capture scaling V0012 with scoped series identity; V009-isolated."
    )
    ap.add_argument("--stage", required=True, choices=["quality-audit", "plan-batch", "capture-batch", "revision-diff"])
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--symbols")
    ap.add_argument("--symbols-file")
    ap.add_argument("--symbol")
    ap.add_argument("--run-day")
    ap.add_argument("--daily-budget", type=int)
    args = ap.parse_args()

    cfg = load(Path(args.config))
    if args.daily_budget is not None:
        cfg["request_policy"]["default_daily_request_budget"] = int(args.daily_budget)
    db_path = ROOT / cfg["capture_db"]

    if args.stage == "quality-audit":
        out = quality_audit(db_path)
    elif args.stage == "revision-diff":
        if not args.symbol:
            raise SystemExit("--symbol required for revision-diff")
        out = revision_diff(db_path, args.symbol)
    else:
        symbols = read_symbols(args.symbols, args.symbols_file)
        if not symbols:
            symbols = list(cfg["cadence_policy"].get("deep_cohort", []))
        plan = plan_due_symbols(db_path, symbols, cfg)
        if args.stage == "plan-batch":
            out = plan
            budget = int(plan["request_budget"])
            deep_count = len(cfg["cadence_policy"].get("deep_cohort", []))
            broad_capacity = max(0, budget - deep_count)
            broad_universe = max(0, len(symbols) - deep_count)
            out["budget_diagnostics"] = {
                "configured_budget": budget,
                "deep_cohort_size": deep_count,
                "nominal_requests_remaining_after_deep_day": broad_capacity,
                "broad_symbols_considered": broad_universe,
                "minimum_days_for_one_broad_rotation_at_nominal_remaining_budget": (
                    None if broad_capacity <= 0 else (broad_universe + broad_capacity - 1) // broad_capacity
                ),
                "note": (
                    "A weekly 497-symbol refresh is not feasible under a 20-25 request/day standard free budget. "
                    "Use rotation or an explicitly verified higher entitlement; do not silently exceed provider limits."
                ),
            }
        else:
            key = api_key_from_env("ALPHAVANTAGE_API_KEY")
            out = capture_selected(db_path, key, plan["selected"], cfg, run_day=args.run_day)
            out["plan_summary"] = {k: plan[k] for k in (
                "symbols_considered", "symbols_due", "request_budget", "selected_count", "deferred_due_count"
            )}

    out["contract_version"] = cfg["contract_version"]
    out["v009_interaction"] = "NONE"
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

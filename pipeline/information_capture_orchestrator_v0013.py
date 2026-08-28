from __future__ import annotations

import argparse
import json
import time
from datetime import datetime, timezone
from pathlib import Path

from ingestion.expectations.alphavantage_expectations_v001 import api_key_from_env, capture_estimates_for_symbol
from ingestion.expectations.alphavantage_scaling_v0011 import plan_due_symbols, read_symbols
from research.information_sources.orchestrator_v0013 import (
    apply_schema, backfill_successful_provider_requests, normalize_calendar_windows,
    requests_in_window, log_request_start, log_request_finish, coverage_audit,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/information_capture_orchestrator_v0013.json"


def load(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Quota-aware strict-PIT expectation capture orchestrator V0013.")
    ap.add_argument("--stage", required=True, choices=[
        "init", "quota-status", "coverage-audit", "plan", "capture"
    ])
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--symbols")
    ap.add_argument("--symbols-file")
    args = ap.parse_args()
    cfg = load(Path(args.config))
    db = ROOT / cfg["capture_db"]
    schema = ROOT / cfg["schema_additive"]

    if args.stage == "init":
        apply_schema(db, schema)
        b = backfill_successful_provider_requests(db)
        c = normalize_calendar_windows(db)
        q = requests_in_window(db, "alpha_vantage", int(cfg["quota_policy"]["rolling_window_hours"]))
        out = {"status":"PASS","backfill":b,"calendar_windows":c,"quota":q}
    elif args.stage == "quota-status":
        out = quota_payload(db, cfg)
    elif args.stage == "coverage-audit":
        out = coverage_audit(db)
    else:
        symbols = read_symbols(args.symbols, args.symbols_file)
        if not symbols:
            symbols = list(cfg["cadence_policy"]["deep_cohort"])
        quota = quota_payload(db, cfg)
        remaining = int(quota["remaining_requests"])
        plan_cfg = {
            "request_policy": {"default_daily_request_budget": remaining},
            "cadence_policy": cfg["cadence_policy"],
            "feature_visibility": cfg["feature_visibility"],
        }
        plan = plan_due_symbols(db, symbols, plan_cfg)
        plan["quota"] = quota
        if args.stage == "plan":
            out = plan
        else:
            if remaining <= 0 or not plan["selected"]:
                out = {
                    "status":"PASS_NO_REQUESTS",
                    "selected_count":0,
                    "quota":quota,
                    "reason":"rolling quota exhausted or no symbols due",
                }
            else:
                key = api_key_from_env("ALPHAVANTAGE_API_KEY")
                results=[]; failures={}
                pause=float(cfg["request_policy"]["minimum_seconds_between_requests"])
                for idx,item in enumerate(plan["selected"]):
                    if idx:
                        time.sleep(max(0.0,pause))
                    sym=item["symbol"]
                    started=datetime.now(timezone.utc).isoformat()
                    req_id=log_request_start(db,"alpha_vantage","EARNINGS_ESTIMATES",sym,started)
                    try:
                        result=capture_estimates_for_symbol(
                            db,key,sym,timeout=int(cfg["request_policy"]["request_timeout_seconds"])
                        )
                        results.append(result)
                        log_request_finish(db,req_id,"SUCCESS",metadata={"result_status":result.get("status")})
                    except Exception as exc:
                        failures[sym]=str(exc)
                        log_request_finish(db,req_id,"FAILED",metadata={"error":str(exc)})
                out={
                    "status":"PASS" if not failures else "PARTIAL",
                    "selected_count":len(plan["selected"]),
                    "results_this_run":results,
                    "failures":failures,
                    "quota_after":quota_payload(db,cfg),
                }

    out["contract_version"]=cfg["contract_version"]
    out["feature_visibility"]=cfg["feature_visibility"]
    out["v009_interaction"]="NONE"
    print(json.dumps(out,indent=2,sort_keys=True))


def quota_payload(db: Path, cfg: dict) -> dict:
    hours=int(cfg["quota_policy"]["rolling_window_hours"])
    cap=int(cfg["quota_policy"]["internal_request_cap"])
    q=requests_in_window(db,"alpha_vantage",hours)
    q["internal_request_cap"]=cap
    q["remaining_requests"]=max(0,cap-int(q["requests"]))
    q["policy"]="rolling_window_conservative_internal_cap"
    return q


if __name__=="__main__":
    main()

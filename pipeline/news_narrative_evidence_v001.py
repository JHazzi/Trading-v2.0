from __future__ import annotations

import argparse
import json
from datetime import datetime, timedelta, timezone
from pathlib import Path

from ingestion.news.alphavantage_news_evidence_v001 import (
    api_key_from_env, fetch_news_sentiment
)
from research.news.news_evidence_foundation_v001 import (
    apply_schema, audit, insert_normalized_feed, insert_source_snapshot
)
from research.information_sources.orchestrator_v0013 import (
    log_request_start, log_request_finish, requests_in_window
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config/news_narrative_evidence_v001.json"


def load_cfg(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def main() -> None:
    ap = argparse.ArgumentParser(description="Strict-PIT news/narrative evidence capture V001.")
    ap.add_argument("--stage", required=True, choices=["init","plan","capture-market","audit"])
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    args = ap.parse_args()
    cfg = load_cfg(Path(args.config))
    db = ROOT / cfg["capture_db"]
    schema = ROOT / cfg["schema_additive"]

    orchestrator_cfg_path = ROOT / "config" / "information_capture_orchestrator_v0013.json"
    orchestrator_cfg = json.loads(orchestrator_cfg_path.read_text(encoding="utf-8"))
    quota_raw = requests_in_window(
        db, "alpha_vantage", int(orchestrator_cfg["quota_policy"]["rolling_window_hours"])
    )
    cap = int(orchestrator_cfg["quota_policy"]["internal_request_cap"])
    quota = {
        **quota_raw,
        "internal_request_cap": cap,
        "remaining_requests": max(0, cap - int(quota_raw["requests"])),
        "policy": "rolling_window_conservative_internal_cap",
    }

    if args.stage == "init":
        apply_schema(db, schema)
        out = {"status":"PASS","database":str(db),"schema":str(schema),"quota":quota}
    elif args.stage == "plan":
        out = {
            "status":"READY",
            "capture_mode":"market_wide_single_call",
            "provider":"Alpha Vantage NEWS_SENTIMENT",
            "provider_enabled": bool(cfg["provider"]["enabled"]),
            "default_limit": int(cfg["provider"]["limit"]),
            "feature_visibility":"BLOCKED",
            "story_clustering":"BLOCKED",
            "sentiment_policy":"provider annotation only; never map positive/negative directly to return sign",
            "quota": quota,
            "quota_note":"Capture refuses to call the provider when the V0013 rolling ledger has no remaining requests.",
        }
    elif args.stage == "audit":
        out = audit(db)
    else:
        if not cfg["provider"]["enabled"]:
            raise SystemExit("provider disabled in config/news_narrative_evidence_v001.json")
        if int(quota["remaining_requests"]) <= 0:
            raise SystemExit("V0013 rolling quota exhausted; NEWS_SENTIMENT call blocked")
        # Deliberately one broad request, not N ticker requests.
        key = api_key_from_env()
        now = datetime.now(timezone.utc)
        lookback = int(cfg["capture_policy"]["lookback_minutes"])
        time_from = (now - timedelta(minutes=lookback)).strftime("%Y%m%dT%H%M")
        requested_at = datetime.now(timezone.utc).isoformat()
        request_id = log_request_start(
            db, "alpha_vantage", "NEWS_SENTIMENT", None, requested_at
        )
        try:
            raw, payload, retrieved_at = fetch_news_sentiment(
                key,
                time_from=time_from,
                limit=int(cfg["provider"]["limit"]),
                sort="LATEST",
                timeout=int(cfg["provider"]["timeout_seconds"]),
            )
            source_id, source_inserted = insert_source_snapshot(
                db,
                raw_payload=raw,
                retrieved_at=retrieved_at,
                source_ref="alpha_vantage:NEWS_SENTIMENT:market_wide",
            )
            norm = insert_normalized_feed(
                db,
                source_observation_id=source_id,
                feed=list(payload.get("feed") or []),
                retrieved_at=retrieved_at,
            )
            log_request_finish(
                db, request_id, "SUCCESS", source_observation_id=source_id,
                metadata={"provider_items": len(payload.get("feed") or [])}
            )
            quota_after_raw = requests_in_window(
                db, "alpha_vantage",
                int(orchestrator_cfg["quota_policy"]["rolling_window_hours"])
            )
            quota_after = {
                **quota_after_raw,
                "internal_request_cap": cap,
                "remaining_requests": max(0, cap - int(quota_after_raw["requests"])),
                "policy": "rolling_window_conservative_internal_cap",
            }
            out = {
                "status":"PASS_SOURCE_CAPTURED",
                "retrieved_at":retrieved_at,
                "provider_items":len(payload.get("feed") or []),
                "source_inserted":int(source_inserted),
                "quota_after": quota_after,
                **norm,
            }
        except Exception as exc:
            log_request_finish(
                db, request_id, "FAILED",
                metadata={"error": str(exc)}
            )
            raise

    out["contract_version"]=cfg["contract_version"]
    out["v009_interaction"]="NONE"
    print(json.dumps(out, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

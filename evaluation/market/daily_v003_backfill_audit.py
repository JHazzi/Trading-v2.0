from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from evaluation.market.daily_v003_foundation_audit import audit_database
from ingestion.prices.yahoo_daily_broad_v003 import (
    DEFAULT_CHECKPOINT,
    DEFAULT_CONFIG,
    DEFAULT_MANIFEST,
    checkpoint_summary,
    load_checkpoint,
    load_config,
    load_manifest,
    plan_audit,
)

TARGET_PANEL_ASSETS = 300
TARGET_HISTORY_5Y_ASSETS = 300


def audit_backfill(
    *,
    db: Path,
    config_path: Path = DEFAULT_CONFIG,
    manifest_path: Path = DEFAULT_MANIFEST,
    checkpoint_path: Path = DEFAULT_CHECKPOINT,
) -> dict[str, Any]:
    config = load_config(config_path)
    foundation = audit_database(db)
    manifest = load_manifest(manifest_path, config)
    plan = plan_audit(
        db=db,
        config=config,
        manifest_path=manifest_path,
    )
    checkpoint = load_checkpoint(
        checkpoint_path,
        config,
        manifest,
    )
    checkpoint_info = checkpoint_summary(checkpoint)

    history = foundation["daily_quality_gated"][
        "assets_by_minimum_history_days"
    ]
    dynamic = foundation["dynamic_panel_readiness"]
    latest = dynamic.get("latest") or {}
    ready_253 = int(latest.get("eligible_253", 0))
    assets_1260 = int(history.get("1260", 0))

    failures = []
    reviews = []

    if foundation["status"] != "PASS":
        failures.append("foundation_audit_failed")
    if plan["status"] != "PASS":
        failures.append("discovery_plan_not_pass")
    if checkpoint_info["failed_tickers"]:
        reviews.append("backfill_failures_present")
    if ready_253 < TARGET_PANEL_ASSETS:
        reviews.append("fewer_than_300_assets_ready_for_253_day_state")
    if assets_1260 < TARGET_HISTORY_5Y_ASSETS:
        reviews.append("fewer_than_300_assets_with_5y_history")

    if failures:
        status = "FAIL"
    elif reviews:
        status = "REVIEW"
    else:
        status = "PASS"

    return {
        "status": status,
        "failures": failures,
        "reviews": reviews,
        "targets": {
            "minimum_assets_ready_for_253_day_state": TARGET_PANEL_ASSETS,
            "minimum_assets_with_1260_days": TARGET_HISTORY_5Y_ASSETS,
        },
        "plan": plan,
        "checkpoint": checkpoint_info,
        "panel": {
            "assets_with_daily_data": foundation["daily_quality_gated"][
                "assets_with_daily_data"
            ],
            "assets_by_minimum_history_days": history,
            "dynamic_panel_readiness": dynamic,
            "sector_readiness_latest": foundation[
                "sector_readiness_latest"
            ],
            "corporate_actions": foundation["corporate_actions"],
            "strict_pit_observation_rows": foundation[
                "daily_quality_gated"
            ]["strict_pit_observation_rows"],
        },
        "deferred": {
            "market_proxies": foundation["recommendation"][
                "missing_core_market_proxies_5y"
            ],
            "macro_usable_now": foundation["macro"][
                "usable_for_market_v003_features_now"
            ],
            "historical_membership_survivorship_free": False,
        },
        "interpretation": (
            "PASS means the current-cohort daily panel is broad enough to "
            "build the first Market Daily V003 all-asset-day dataset. "
            "It does not mean strict historical PIT or survivorship-free "
            "membership has been achieved."
        ),
    }

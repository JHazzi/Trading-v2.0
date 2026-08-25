from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.market.daily_v003_backfill_audit import audit_backfill
from ingestion.prices.yahoo_daily_broad_v003 import (
    DEFAULT_CHECKPOINT,
    DEFAULT_CONFIG,
    DEFAULT_DB,
    DEFAULT_MANIFEST,
    DEFAULT_RAW_ROOT,
    discover_pending,
    load_config,
    plan_audit,
    preflight,
    run_backfill,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_AUDIT_REPORT = (
    ROOT / "reports" / "market_brain_daily_v003" / "broad_backfill_audit.json"
)


def _write(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--stage",
        required=True,
        choices=(
            "preflight",
            "discover",
            "plan-audit",
            "smoke",
            "backfill",
            "audit",
        ),
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--manifest", type=Path, default=DEFAULT_MANIFEST)
    p.add_argument("--checkpoint", type=Path, default=DEFAULT_CHECKPOINT)
    p.add_argument("--limit", type=int)
    p.add_argument("--retry-errors", action="store_true")
    p.add_argument("--retry-failed", action="store_true")
    p.add_argument("--progress-every", type=int, default=25)
    p.add_argument("--sleep-seconds", type=float)
    p.add_argument("--report", type=Path, default=DEFAULT_AUDIT_REPORT)
    args = p.parse_args()

    config = load_config(args.config)
    sleep_seconds = (
        float(args.sleep_seconds)
        if args.sleep_seconds is not None
        else float(config["sleep_seconds_between_provider_calls"])
    )

    if args.stage == "preflight":
        result = preflight(args.db, config)
    elif args.stage == "discover":
        result = discover_pending(
            db=args.db,
            config=config,
            manifest_path=args.manifest,
            limit=args.limit,
            retry_errors=args.retry_errors,
            progress_every=args.progress_every,
            sleep_seconds=sleep_seconds,
        )
    elif args.stage == "plan-audit":
        result = plan_audit(
            db=args.db,
            config=config,
            manifest_path=args.manifest,
        )
    elif args.stage == "smoke":
        result = run_backfill(
            db=args.db,
            raw_root=args.raw_root,
            config=config,
            manifest_path=args.manifest,
            checkpoint_path=args.checkpoint,
            limit=5 if args.limit is None else args.limit,
            retry_failed=args.retry_failed,
            progress_every=args.progress_every,
            sleep_seconds=sleep_seconds,
        )
    elif args.stage == "backfill":
        result = run_backfill(
            db=args.db,
            raw_root=args.raw_root,
            config=config,
            manifest_path=args.manifest,
            checkpoint_path=args.checkpoint,
            limit=args.limit,
            retry_failed=args.retry_failed,
            progress_every=args.progress_every,
            sleep_seconds=sleep_seconds,
        )
    else:
        result = audit_backfill(
            db=args.db,
            config_path=args.config,
            manifest_path=args.manifest,
            checkpoint_path=args.checkpoint,
        )
        _write(args.report, result)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

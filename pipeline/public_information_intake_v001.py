from __future__ import annotations

import argparse
import json
from pathlib import Path

from ingestion.public_information.intake_v001 import (
    IntakeError,
    audit_snapshot,
    build_plan,
    download_snapshot,
    freeze_remote_manifest,
    initialize_catalog,
    load_config,
    report_path,
    sample_snapshot,
    write_json,
)


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "public_information_intake_v001.json"


def _progress(payload: dict) -> None:
    print(json.dumps(payload, sort_keys=True), flush=True)


def main() -> None:
    parser = argparse.ArgumentParser(
        description=(
            "Versioned public-information intake. Writes only its isolated catalog, "
            "raw/lake roots and reports; training and V009 interaction remain blocked."
        )
    )
    parser.add_argument(
        "--stage",
        required=True,
        choices=["plan", "init", "manifest", "download", "audit", "sample"],
    )
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument(
        "--dataset",
        choices=["alpaca_hf_bars_1m", "financial_news_multisource"],
    )
    parser.add_argument("--profile")
    parser.add_argument("--audit-level", choices=["integrity", "metadata", "full"], default="metadata")
    parser.add_argument("--sample-kind", choices=["bars", "news"])
    parser.add_argument("--max-files", type=int)
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument("--timeout", type=int, default=60)
    args = parser.parse_args()

    config_path = Path(args.config).resolve()
    config = load_config(config_path)
    try:
        if args.stage == "plan":
            result = build_plan(ROOT, config)
            path = report_path(ROOT, config, "plan")
            result["report_sha256"] = write_json(path, result)
            result["report_path"] = str(path)
        elif args.stage == "init":
            result = initialize_catalog(ROOT, config)
            path = report_path(ROOT, config, "init")
            result["report_sha256"] = write_json(path, result)
            result["report_path"] = str(path)
        elif args.stage == "sample":
            if not args.sample_kind:
                parser.error("--sample-kind is required for --stage sample")
            result = sample_snapshot(ROOT, config, args.sample_kind)
        else:
            if not args.dataset or not args.profile:
                parser.error("--dataset and --profile are required for this stage")
            if args.stage == "manifest":
                result = freeze_remote_manifest(
                    ROOT,
                    config,
                    args.dataset,
                    args.profile,
                    timeout=args.timeout,
                )
                path = report_path(
                    ROOT,
                    config,
                    "manifest",
                    args.dataset,
                    result["snapshot_id"],
                )
                result["report_sha256"] = write_json(path, result)
                result["report_path"] = str(path)
            elif args.stage == "download":
                result = download_snapshot(
                    ROOT,
                    config,
                    args.dataset,
                    args.profile,
                    max_files=args.max_files,
                    dry_run=args.dry_run,
                    progress=_progress,
                )
            else:
                result = audit_snapshot(
                    ROOT,
                    config,
                    args.dataset,
                    args.profile,
                    level=args.audit_level,
                )
    except IntakeError as exc:
        raise SystemExit(f"BLOCKED: {exc}") from exc

    print(json.dumps(result, indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()

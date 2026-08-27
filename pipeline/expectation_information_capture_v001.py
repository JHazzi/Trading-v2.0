from __future__ import annotations

import argparse
import json
from pathlib import Path

from ingestion.expectations.foundation_v001 import (
    CaptureContractError,
    audit_db,
    init_db,
    ingest_records,
    load_jsonl,
    manifest,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "expectation_information_capture_v001.json"


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve(root: Path, value: str) -> Path:
    p = Path(value)
    return p if p.is_absolute() else root / p


def ensure_isolated(db_path: Path, config: dict, root: Path) -> None:
    resolved = db_path.resolve()
    for protected in config.get("protected_paths", []):
        pp = resolve(root, protected).resolve()
        if resolved == pp:
            raise CaptureContractError(f"capture DB cannot use protected V009/market path: {pp}")
    if db_path.name == "market_data.db":
        raise CaptureContractError("capture foundation must never write market_data.db")


def write_report(report_dir: Path, name: str, payload: dict) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    (report_dir / name).write_text(json.dumps(payload, indent=2, sort_keys=True) + "\n", encoding="utf-8")


def stage_plan(config_path: Path) -> dict:
    cfg = load_config(config_path)
    db_path = resolve(ROOT, cfg["database_path"])
    schema_path = resolve(ROOT, cfg["schema_path"])
    ensure_isolated(db_path, cfg, ROOT)
    return {
        "contract_version": cfg["contract_version"],
        "status": "READY",
        "database_path": str(db_path),
        "schema_path": str(schema_path),
        "schema_exists": schema_path.exists(),
        "database_is_separate_from_market_core": db_path.name != "market_data.db",
        "feature_visibility": cfg["feature_visibility"],
        "strict_pit_policy": cfg["strict_pit_policy"],
        "next_action": "init only; capture data remains model-invisible",
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", default=str(DEFAULT_CONFIG))
    ap.add_argument("--stage", required=True, choices=["plan", "init", "ingest-jsonl", "audit", "manifest"])
    ap.add_argument("--input")
    ap.add_argument("--cutoff-available-at")
    args = ap.parse_args()

    config_path = Path(args.config).resolve()
    cfg = load_config(config_path)
    db_path = resolve(ROOT, cfg["database_path"])
    schema_path = resolve(ROOT, cfg["schema_path"])
    report_dir = resolve(ROOT, cfg["report_dir"])
    ensure_isolated(db_path, cfg, ROOT)

    if args.stage == "plan":
        payload = stage_plan(config_path)
        write_report(report_dir, "capture_plan.json", payload)
    elif args.stage == "init":
        init_db(db_path, schema_path)
        payload = {"status": "INITIALIZED", "database_path": str(db_path), **audit_db(db_path)}
        write_report(report_dir, "init_report.json", payload)
    elif args.stage == "ingest-jsonl":
        if not args.input:
            raise SystemExit("--input is required for ingest-jsonl")
        init_db(db_path, schema_path)
        stats = ingest_records(db_path, load_jsonl(Path(args.input)))
        payload = {"status": "PASS", **stats, "audit": audit_db(db_path)}
        write_report(report_dir, "last_ingest_report.json", payload)
    elif args.stage == "audit":
        init_db(db_path, schema_path)
        payload = audit_db(db_path)
        write_report(report_dir, "audit.json", payload)
    else:
        init_db(db_path, schema_path)
        payload = manifest(db_path, args.cutoff_available_at)
        write_report(report_dir, "capture_manifest.json", payload)

    print(json.dumps(payload, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

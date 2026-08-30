"""Build and audit the immutable, external V002 outcome-selection mask.

The current reviewed dataset has no quarantined model-visible actions, so the
expected mask is empty.  The implementation is nevertheless complete: if a
future evidence decision quarantines an action, only outcome paths containing
that exact step are excluded.  V002 itself is always opened read-only.
"""
from __future__ import annotations

import argparse
from contextlib import closing
import hashlib
import json
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Iterable


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_V002 = ROOT / "data" / "processed" / "market_temporal_v002.db"
DEFAULT_REVIEW = ROOT / "reports" / "market_temporal_v002_review" / "economic_action_review.json"
DEFAULT_CONFIG = ROOT / "config" / "temporal_v002_selection_mask_v001.json"
DEFAULT_REPORT_DIR = ROOT / "reports" / "market_temporal_v002_selection_mask_v001"


def digest(path: Path, chunk: int = 8 * 1024 * 1024) -> str:
    value = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(chunk):
            value.update(block)
    return value.hexdigest()


def atomic_json(path: Path, payload: dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(prefix=f".{path.name}.", dir=path.parent)
    try:
        with os.fdopen(fd, "w", encoding="utf-8") as handle:
            json.dump(payload, handle, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False)
            handle.write("\n")
            handle.flush()
            os.fsync(handle.fileno())
        os.replace(temporary, path)
    finally:
        Path(temporary).unlink(missing_ok=True)


def ro_connect(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro&immutable=1", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def load_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if payload.get("version") != "market_temporal_v002_selection_mask_v001":
        raise ValueError("unsupported_selection_mask_contract")
    if payload.get("source_database_mutation_allowed") is not False:
        raise ValueError("selection_mask_may_not_mutate_source")
    return payload


def display_path(path: Path) -> str:
    try:
        return str(path.resolve().relative_to(ROOT))
    except ValueError:
        return str(path.resolve())


def quarantined_events(review: dict[str, Any]) -> list[dict[str, Any]]:
    result = []
    for event in review.get("flagged_events", []):
        if not event.get("decision_required"):
            continue
        disposition = str(event.get("decision", {}).get("disposition", event.get("disposition", "")))
        if disposition in {"quarantine", "quarantined", "exclude_from_training"}:
            result.append(event)
    # The review also exposes a canonical list in newer report revisions.
    quarantined_ids = {str(x) for x in review.get("quarantined_review_ids", [])}
    known = {str(x.get("review_id")) for x in result}
    for event in review.get("flagged_events", []):
        if str(event.get("review_id")) in quarantined_ids - known:
            result.append(event)
    return sorted(result, key=lambda x: str(x.get("review_id")))


def affected_rows(conn: sqlite3.Connection, event: dict[str, Any]) -> Iterable[tuple[int, int, str, int, str]]:
    review_id = str(event["review_id"])
    asset_id = int(event["asset_id"])
    trading_day = str(event["trading_day"])
    step = conn.execute(
        "SELECT asset_session_index FROM temporal_return_steps WHERE asset_id=? AND trading_day=?",
        (asset_id, trading_day),
    ).fetchone()
    if step is None:
        raise RuntimeError(f"quarantined_action_step_missing:{review_id}")
    action_index = int(step[0])
    rows = conn.execute(
        "SELECT o.origin_id,o.tau_sessions,s.state_id FROM temporal_outcomes o "
        "JOIN temporal_origins s USING(origin_id) WHERE s.asset_id=? "
        "AND s.origin_session_index<? AND s.origin_session_index+o.tau_sessions>=? "
        "AND o.total_return_label_status='usable' ORDER BY o.origin_id,o.tau_sessions",
        (asset_id, action_index, action_index),
    )
    for row in rows:
        yield int(row["origin_id"]), int(row["tau_sessions"]), review_id, action_index, str(row["state_id"])


def build_mask(v002: Path, review_path: Path, config_path: Path, output_dir: Path) -> dict[str, Any]:
    cfg = load_contract(config_path)
    review = json.loads(review_path.read_text(encoding="utf-8"))
    events = quarantined_events(review)
    output_dir.mkdir(parents=True, exist_ok=True)
    target = output_dir / "selection_mask.sqlite"
    fd, name = tempfile.mkstemp(prefix=".selection_mask.", suffix=".sqlite", dir=output_dir)
    os.close(fd)
    temporary = Path(name)
    try:
        out = sqlite3.connect(temporary)
        out.executescript(
            "CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);"
            "CREATE TABLE excluded_outcomes("
            "origin_id INTEGER NOT NULL,tau_sessions INTEGER NOT NULL,review_id TEXT NOT NULL,"
            "action_session_index INTEGER NOT NULL,state_id TEXT NOT NULL,"
            "PRIMARY KEY(origin_id,tau_sessions,review_id));"
            "CREATE INDEX idx_selection_pair ON excluded_outcomes(origin_id,tau_sessions);"
        )
        inserted = 0
        with closing(ro_connect(v002)) as source:
            for event in events:
                batch = []
                for row in affected_rows(source, event):
                    batch.append(row)
                    if len(batch) == 100_000:
                        out.executemany("INSERT OR IGNORE INTO excluded_outcomes VALUES(?,?,?,?,?)", batch)
                        inserted += out.execute("SELECT changes()").fetchone()[0]
                        batch.clear()
                if batch:
                    before = out.total_changes
                    out.executemany("INSERT OR IGNORE INTO excluded_outcomes VALUES(?,?,?,?,?)", batch)
                    inserted += out.total_changes - before
        metadata = {
            "version": cfg["version"],
            "v002_sha256": digest(v002),
            "review_sha256": digest(review_path),
            "config_sha256": digest(config_path),
            "quarantined_review_ids": [str(x["review_id"]) for x in events],
            "excluded_rows": int(out.execute("SELECT COUNT(*) FROM excluded_outcomes").fetchone()[0]),
            "excluded_unique_pairs": int(out.execute(
                "SELECT COUNT(*) FROM (SELECT origin_id,tau_sessions FROM excluded_outcomes GROUP BY 1,2)"
            ).fetchone()[0]),
        }
        out.executemany(
            "INSERT INTO metadata VALUES(?,?)",
            [(key, json.dumps(value, sort_keys=True, separators=(",", ":"))) for key, value in metadata.items()],
        )
        out.commit()
        out.execute("PRAGMA optimize")
        out.close()
        os.replace(temporary, target)
    finally:
        temporary.unlink(missing_ok=True)
    result = {
        **metadata,
        "stage": "build",
        "status": "PASS",
        "selection_mask_path": display_path(target),
        "source_opened_read_only": True,
        "source_mutated": False,
    }
    atomic_json(output_dir / "build_report.json", result)
    return result


def audit_mask(v002: Path, review_path: Path, config_path: Path, output_dir: Path) -> dict[str, Any]:
    cfg = load_contract(config_path)
    mask_path = output_dir / "selection_mask.sqlite"
    failures: list[str] = []
    if not mask_path.exists():
        failures.append("SELECTION_MASK_MISSING_RUN_BUILD")
        metadata: dict[str, Any] = {}
    else:
        with closing(ro_connect(mask_path)) as mask:
            metadata = {str(r["key"]): json.loads(str(r["value_json"])) for r in mask.execute("SELECT * FROM metadata")}
            duplicate_pairs = int(mask.execute(
                "SELECT COUNT(*) FROM (SELECT origin_id,tau_sessions FROM excluded_outcomes GROUP BY 1,2 HAVING COUNT(*)>1)"
            ).fetchone()[0])
            # Multiple quarantined events can legitimately affect one path. They
            # are retained for lineage, so duplicate pairs are informational.
            stored_rows = int(mask.execute("SELECT COUNT(*) FROM excluded_outcomes").fetchone()[0])
            stored_pairs = int(mask.execute(
                "SELECT COUNT(*) FROM (SELECT origin_id,tau_sessions FROM excluded_outcomes GROUP BY 1,2)"
            ).fetchone()[0])
        if metadata.get("v002_sha256") != digest(v002):
            failures.append("V002_HASH_CHANGED_AFTER_MASK_BUILD")
        if metadata.get("review_sha256") != digest(review_path):
            failures.append("REVIEW_HASH_CHANGED_AFTER_MASK_BUILD")
        if metadata.get("config_sha256") != digest(config_path):
            failures.append("MASK_CONFIG_HASH_CHANGED_AFTER_BUILD")
        if metadata.get("v002_sha256") != cfg.get("expected_v002_sha256"):
            failures.append("V002_HASH_DIFFERS_FROM_FROZEN_CONTRACT")
        if stored_rows != int(metadata.get("excluded_rows", -1)):
            failures.append("MASK_ROW_COUNT_METADATA_MISMATCH")
        if stored_pairs != int(metadata.get("excluded_unique_pairs", -1)):
            failures.append("MASK_PAIR_COUNT_METADATA_MISMATCH")
    result = {
        "version": cfg["version"],
        "stage": "audit",
        "status": "PASS" if not failures else "FAIL",
        "failures": failures,
        "mask_sha256": digest(mask_path) if mask_path.exists() else None,
        "v002_sha256": digest(v002),
        "review_sha256": digest(review_path),
        "quarantined_review_ids": metadata.get("quarantined_review_ids", []),
        "excluded_rows": metadata.get("excluded_rows"),
        "excluded_unique_pairs": metadata.get("excluded_unique_pairs"),
        "duplicate_pair_lineages": duplicate_pairs if mask_path.exists() else None,
        "empty_mask_is_valid": not metadata.get("quarantined_review_ids", []),
        "source_opened_read_only": True,
        "source_mutated": False,
    }
    atomic_json(output_dir / "audit.json", result)
    return result


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("build", "audit", "all"), default="all")
    parser.add_argument("--v002-db", type=Path, default=DEFAULT_V002)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()
    result: dict[str, Any] | None = None
    if args.stage in {"build", "all"}:
        result = build_mask(args.v002_db, args.review, args.config, args.output_dir)
    if args.stage in {"audit", "all"}:
        result = audit_mask(args.v002_db, args.review, args.config, args.output_dir)
    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))
    if result and result["status"] != "PASS":
        raise SystemExit(2)


if __name__ == "__main__":
    main()

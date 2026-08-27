from __future__ import annotations

import hashlib
import json
import sqlite3
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Any, Iterable

CONTRACT_VERSION = "expectation_information_capture_v001"
KINDS = {
    "source_observation",
    "scheduled_event_observation",
    "expectation_observation",
    "economic_fact_observation",
}


class CaptureContractError(ValueError):
    pass


def canonical_json(value: Any) -> str:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False)


def sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def deterministic_observation_id(kind: str, payload: dict[str, Any]) -> str:
    stable = {k: v for k, v in payload.items() if k not in {"observation_id", "created_at"}}
    return f"{kind}-" + sha256_text(canonical_json(stable))[:32]


def parse_aware_iso(value: str | None, field: str, *, required: bool = False) -> datetime | None:
    if value in (None, ""):
        if required:
            raise CaptureContractError(f"{field} is required")
        return None
    text = str(value).replace("Z", "+00:00")
    try:
        dt = datetime.fromisoformat(text)
    except ValueError as exc:
        raise CaptureContractError(f"{field} must be ISO-8601: {value}") from exc
    if dt.tzinfo is None or dt.utcoffset() is None:
        raise CaptureContractError(f"{field} must be timezone-aware: {value}")
    return dt


def _strict_pit(value: Any) -> int:
    if value in (1, True):
        return 1
    if value in (0, False):
        return 0
    raise CaptureContractError("strict_pit must be boolean/0/1")


def validate_source(payload: dict[str, Any]) -> None:
    for field in ("source_type", "source_name", "retrieved_at", "available_at"):
        if not payload.get(field):
            raise CaptureContractError(f"source_observation.{field} is required")
    strict = _strict_pit(payload.get("strict_pit"))
    published = parse_aware_iso(payload.get("published_at"), "published_at")
    first_seen = parse_aware_iso(payload.get("first_seen_at"), "first_seen_at")
    retrieved = parse_aware_iso(payload.get("retrieved_at"), "retrieved_at", required=True)
    available = parse_aware_iso(payload.get("available_at"), "available_at", required=True)
    if first_seen and first_seen > retrieved:
        raise CaptureContractError("first_seen_at cannot be after retrieved_at")
    if published and published > retrieved:
        # Some sources expose corrected timestamps, but those must not be called strict PIT.
        if strict:
            raise CaptureContractError("strict PIT published_at cannot be after retrieved_at")
    if strict:
        if first_seen is None:
            raise CaptureContractError("strict PIT requires first_seen_at")
        # Conservative live-capture rule: the model cannot claim availability before bytes were retrieved.
        if available < retrieved:
            raise CaptureContractError("strict PIT available_at cannot precede retrieved_at")
    # Historical reconstruction/backfill may have an earlier public proxy, but must remain strict_pit=0.


def validate_child(payload: dict[str, Any], kind: str) -> None:
    for field in ("source_observation_id", "available_at"):
        if not payload.get(field):
            raise CaptureContractError(f"{kind}.{field} is required")
    _strict_pit(payload.get("strict_pit"))
    parse_aware_iso(payload.get("available_at"), f"{kind}.available_at", required=True)

    if kind == "scheduled_event_observation":
        for field in ("event_type", "scheduled_for", "event_status"):
            if not payload.get(field):
                raise CaptureContractError(f"{kind}.{field} is required")
        parse_aware_iso(payload.get("scheduled_for"), "scheduled_for", required=True)
    elif kind == "expectation_observation":
        for field in ("entity_key", "expectation_type", "metric_key", "statistic_key"):
            if not payload.get(field):
                raise CaptureContractError(f"{kind}.{field} is required")
        if payload.get("value_real") is None and payload.get("value_text") is None:
            raise CaptureContractError("expectation observation needs value_real or value_text")
        parse_aware_iso(payload.get("provider_as_of"), "provider_as_of")
    elif kind == "economic_fact_observation":
        for field in ("entity_key", "fact_type", "metric_key"):
            if not payload.get(field):
                raise CaptureContractError(f"{kind}.{field} is required")
        if payload.get("value_real") is None and payload.get("value_text") is None:
            raise CaptureContractError("economic fact observation needs value_real or value_text")


def validate_record(record: dict[str, Any]) -> tuple[str, dict[str, Any]]:
    kind = record.get("kind")
    if kind not in KINDS:
        raise CaptureContractError(f"unsupported kind: {kind}")
    payload = dict(record.get("payload") or {})
    if kind == "source_observation":
        validate_source(payload)
    else:
        validate_child(payload, kind)
    if not payload.get("observation_id"):
        payload["observation_id"] = deterministic_observation_id(kind, payload)
    return kind, payload


def connect(db_path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    return conn


def init_db(db_path: Path, schema_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)
    sql = schema_path.read_text(encoding="utf-8")
    with connect(db_path) as conn:
        conn.executescript(sql)
        row = conn.execute("SELECT value FROM capture_meta WHERE key='contract_version'").fetchone()
        if not row or row[0] != CONTRACT_VERSION:
            raise CaptureContractError("capture DB contract_version mismatch")


def _json_or_none(value: Any) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            json.loads(value)
            return value
        except json.JSONDecodeError:
            return canonical_json({"value": value})
    return canonical_json(value)


def insert_record(conn: sqlite3.Connection, kind: str, p: dict[str, Any]) -> bool:
    table = {
        "source_observation": "source_observations",
        "scheduled_event_observation": "scheduled_event_observations",
        "expectation_observation": "expectation_observations",
        "economic_fact_observation": "economic_fact_observations",
    }[kind]

    columns = {
        "source_observation": [
            "observation_id", "source_type", "source_name", "source_ref", "canonical_url",
            "published_at", "first_seen_at", "retrieved_at", "available_at", "strict_pit",
            "content_sha256", "raw_payload_json", "metadata_json",
        ],
        "scheduled_event_observation": [
            "observation_id", "entity_key", "asset_ticker", "event_type", "scheduled_for",
            "event_status", "available_at", "strict_pit", "source_observation_id", "metadata_json",
        ],
        "expectation_observation": [
            "observation_id", "entity_key", "asset_ticker", "expectation_type", "metric_key",
            "fiscal_period", "statistic_key", "value_real", "value_text", "unit", "currency",
            "provider_as_of", "available_at", "strict_pit", "source_observation_id", "metadata_json",
        ],
        "economic_fact_observation": [
            "observation_id", "entity_key", "asset_ticker", "fact_type", "metric_key",
            "fiscal_period", "value_real", "value_text", "unit", "currency", "available_at",
            "strict_pit", "source_observation_id", "metadata_json",
        ],
    }[kind]
    values = []
    for col in columns:
        val = p.get(col)
        if col in {"strict_pit"}:
            val = _strict_pit(val)
        if col in {"raw_payload_json", "metadata_json"}:
            val = _json_or_none(val)
        values.append(val)
    placeholders = ",".join("?" for _ in columns)
    sql = f"INSERT OR IGNORE INTO {table} ({','.join(columns)}) VALUES ({placeholders})"
    before = conn.total_changes
    conn.execute(sql, values)
    return conn.total_changes > before


def _fetch_source(conn: sqlite3.Connection, source_id: str) -> sqlite3.Row:
    row = conn.execute(
        "SELECT observation_id, available_at, strict_pit FROM source_observations WHERE observation_id=?",
        (source_id,),
    ).fetchone()
    if not row:
        raise CaptureContractError(f"unknown source_observation_id: {source_id}")
    return row


def enforce_source_lineage(conn: sqlite3.Connection, kind: str, p: dict[str, Any]) -> None:
    if kind == "source_observation":
        return
    src = _fetch_source(conn, str(p["source_observation_id"]))
    child_available = parse_aware_iso(p["available_at"], "available_at", required=True)
    src_available = parse_aware_iso(src["available_at"], "source.available_at", required=True)
    if child_available < src_available:
        raise CaptureContractError("derived observation available_at cannot precede source available_at")
    if _strict_pit(p["strict_pit"]) and int(src["strict_pit"]) != 1:
        raise CaptureContractError("strict PIT child cannot derive from non-strict-PIT source")


def ingest_records(db_path: Path, records: Iterable[dict[str, Any]]) -> dict[str, int]:
    inserted = 0
    skipped = 0
    by_kind = {k: 0 for k in KINDS}
    with connect(db_path) as conn:
        for record in records:
            kind, payload = validate_record(record)
            enforce_source_lineage(conn, kind, payload)
            if insert_record(conn, kind, payload):
                inserted += 1
                by_kind[kind] += 1
            else:
                skipped += 1
    return {"inserted": inserted, "skipped": skipped, **{f"inserted_{k}": v for k, v in by_kind.items()}}


def load_jsonl(path: Path) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for line_no, line in enumerate(path.read_text(encoding="utf-8").splitlines(), 1):
        if not line.strip():
            continue
        try:
            obj = json.loads(line)
        except json.JSONDecodeError as exc:
            raise CaptureContractError(f"invalid JSONL at line {line_no}") from exc
        if not isinstance(obj, dict):
            raise CaptureContractError(f"JSONL line {line_no} must be an object")
        rows.append(obj)
    return rows


def audit_db(db_path: Path) -> dict[str, Any]:
    with connect(db_path) as conn:
        counts = {}
        strict = 0
        non_strict = 0
        for table in (
            "source_observations",
            "scheduled_event_observations",
            "expectation_observations",
            "economic_fact_observations",
        ):
            counts[table] = conn.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0]
            s = conn.execute(f"SELECT COALESCE(SUM(strict_pit),0), COUNT(*) FROM {table}").fetchone()
            strict += int(s[0])
            non_strict += int(s[1]) - int(s[0])

        orphan = 0
        for table in ("scheduled_event_observations", "expectation_observations", "economic_fact_observations"):
            orphan += conn.execute(
                f"SELECT COUNT(*) FROM {table} c LEFT JOIN source_observations s "
                f"ON c.source_observation_id=s.observation_id WHERE s.observation_id IS NULL"
            ).fetchone()[0]

        invalid_source_time = conn.execute(
            "SELECT COUNT(*) FROM source_observations "
            "WHERE strict_pit=1 AND datetime(available_at) < datetime(retrieved_at)"
        ).fetchone()[0]

        revision_series = conn.execute(
            "SELECT COUNT(*) FROM ("
            "SELECT entity_key, expectation_type, metric_key, COALESCE(fiscal_period,''), statistic_key "
            "FROM expectation_observations GROUP BY 1,2,3,4,5 HAVING COUNT(*) > 1)"
        ).fetchone()[0]

        return {
            "contract_version": CONTRACT_VERSION,
            "status": "PASS" if orphan == 0 and invalid_source_time == 0 else "FAIL",
            "counts": counts,
            "strict_pit_rows": strict,
            "non_strict_pit_rows": non_strict,
            "orphan_child_rows": orphan,
            "invalid_strict_source_time_rows": invalid_source_time,
            "expectation_revision_series": revision_series,
            "feature_visibility": "BLOCKED",
        }


def manifest(db_path: Path, cutoff_available_at: str | None = None) -> dict[str, Any]:
    if cutoff_available_at is not None:
        parse_aware_iso(cutoff_available_at, "cutoff_available_at", required=True)
    audit = audit_db(db_path)
    with connect(db_path) as conn:
        pieces: list[str] = []
        for table in (
            "source_observations",
            "scheduled_event_observations",
            "expectation_observations",
            "economic_fact_observations",
        ):
            where = ""
            args: tuple[Any, ...] = ()
            if cutoff_available_at is not None:
                where = " WHERE available_at <= ?"
                args = (cutoff_available_at,)
            rows = conn.execute(
                f"SELECT * FROM {table}{where} ORDER BY observation_id", args
            ).fetchall()
            for row in rows:
                pieces.append(table + "\0" + canonical_json(dict(row)))
    digest = sha256_text("\n".join(pieces))
    return {
        **audit,
        "cutoff_available_at": cutoff_available_at,
        "canonical_sha256": digest,
        "manifest_rows": len(pieces),
    }

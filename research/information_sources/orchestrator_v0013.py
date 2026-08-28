from __future__ import annotations

import hashlib
import json
import sqlite3
import re
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "information_capture_orchestrator_v0013"


def _conn(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def _stable_id(prefix: str, payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return prefix + "-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def apply_schema(db_path: Path, schema_path: Path) -> None:
    with _conn(db_path) as conn:
        conn.executescript(schema_path.read_text(encoding="utf-8"))


def backfill_successful_provider_requests(db_path: Path) -> dict[str, int]:
    inserted = 0
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT observation_id, source_ref, retrieved_at, metadata_json "
            "FROM source_observations WHERE source_name='Alpha Vantage' "
            "ORDER BY retrieved_at"
        ).fetchall()
        for r in rows:
            ref = str(r["source_ref"] or "")
            if "EARNINGS_CALENDAR" in ref:
                endpoint = "EARNINGS_CALENDAR"; symbol = None
            elif "EARNINGS_ESTIMATES" in ref:
                endpoint = "EARNINGS_ESTIMATES"
                symbol = ref.rsplit(":", 1)[-1].upper() if ":" in ref else None
            else:
                endpoint = ref or "UNKNOWN"; symbol = None
            payload = {
                "provider": "alpha_vantage",
                "endpoint": endpoint,
                "asset_ticker": symbol,
                "requested_at": str(r["retrieved_at"]),
                "source_observation_id": str(r["observation_id"]),
                "backfilled_from_source_observation": True,
            }
            req_id = _stable_id("provider-request", payload)
            before = conn.total_changes
            conn.execute(
                "INSERT OR IGNORE INTO provider_request_observations "
                "(request_id,provider,endpoint,asset_ticker,requested_at,finished_at,status,"
                "source_observation_id,metadata_json) VALUES (?,?,?,?,?,?,?,?,?)",
                (
                    req_id, "alpha_vantage", endpoint, symbol, str(r["retrieved_at"]),
                    str(r["retrieved_at"]), "SUCCESS_BACKFILLED", str(r["observation_id"]),
                    json.dumps({"backfilled_from_source_observation": True}, sort_keys=True),
                ),
            )
            inserted += int(conn.total_changes > before)
    return {"inserted": inserted, "source_rows_seen": len(rows)}


def normalize_calendar_windows(db_path: Path) -> dict[str, int]:
    inserted = 0
    parsed = 0
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT observation_id, available_at, strict_pit, raw_payload_json "
            "FROM source_observations "
            "WHERE source_name='Alpha Vantage' AND source_ref='alpha_vantage:EARNINGS_CALENDAR' "
            "ORDER BY available_at"
        ).fetchall()
        for src in rows:
            try:
                payload = json.loads(src["raw_payload_json"] or "{}")
            except json.JSONDecodeError:
                continue
            for row in payload.get("parsed_rows", []):
                symbol = str(row.get("symbol") or "").strip().upper()
                d = str(row.get("reportDate") or row.get("report_date") or "").strip()
                if not symbol or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", d):
                    continue
                parsed += 1
                item = {
                    "asset_ticker": symbol,
                    "event_type": "earnings_report",
                    "scheduled_date": d,
                    "daypart": None,
                    "time_precision": "date_only",
                    "event_status": "scheduled",
                    "available_at": str(src["available_at"]),
                    "source_observation_id": str(src["observation_id"]),
                }
                obs_id = _stable_id("scheduled-event-window", item)
                before = conn.total_changes
                conn.execute(
                    "INSERT OR IGNORE INTO scheduled_event_window_observations "
                    "(observation_id,entity_key,asset_ticker,event_type,scheduled_date,daypart,"
                    "time_precision,event_status,available_at,strict_pit,source_observation_id,metadata_json) "
                    "VALUES (?,?,?,?,?,?,?,?,?,?,?,?)",
                    (
                        obs_id, symbol, symbol, "earnings_report", d, None, "date_only",
                        "scheduled", str(src["available_at"]), int(src["strict_pit"]),
                        str(src["observation_id"]),
                        json.dumps({"provider_row": row}, sort_keys=True),
                    ),
                )
                inserted += int(conn.total_changes > before)
    return {"inserted": inserted, "parsed_rows": parsed, "calendar_snapshots": len(rows)}


def requests_in_window(db_path: Path, provider: str, hours: int = 24, now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    start = now - timedelta(hours=hours)
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT request_id, endpoint, asset_ticker, requested_at, status "
            "FROM provider_request_observations WHERE provider=? AND requested_at>=? AND requested_at<=? "
            "ORDER BY requested_at",
            (provider, start.isoformat(), now.isoformat()),
        ).fetchall()
    return {
        "provider": provider,
        "window_hours": hours,
        "window_start": start.isoformat(),
        "window_end": now.isoformat(),
        "requests": len(rows),
        "by_status": _count(rows, "status"),
        "by_endpoint": _count(rows, "endpoint"),
    }


def _count(rows, field: str) -> dict[str, int]:
    out: dict[str, int] = {}
    for r in rows:
        key = str(r[field] or "UNKNOWN")
        out[key] = out.get(key, 0) + 1
    return dict(sorted(out.items()))


def log_request_start(db_path: Path, provider: str, endpoint: str, symbol: str | None, requested_at: str) -> str:
    payload = {
        "provider": provider, "endpoint": endpoint, "asset_ticker": symbol,
        "requested_at": requested_at,
    }
    req_id = _stable_id("provider-request", payload)
    with _conn(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO provider_request_observations "
            "(request_id,provider,endpoint,asset_ticker,requested_at,status,metadata_json) "
            "VALUES (?,?,?,?,?,?,?)",
            (req_id, provider, endpoint, symbol, requested_at, "ATTEMPTED", "{}"),
        )
    return req_id


def log_request_finish(db_path: Path, request_id: str, status: str, source_observation_id: str | None = None, metadata: dict[str, Any] | None = None) -> None:
    finished_at = datetime.now(timezone.utc).isoformat()
    with _conn(db_path) as conn:
        conn.execute(
            "UPDATE provider_request_observations SET finished_at=?, status=?, source_observation_id=?, metadata_json=? "
            "WHERE request_id=?",
            (finished_at, status, source_observation_id, json.dumps(metadata or {}, sort_keys=True), request_id),
        )


def latest_event_dates(db_path: Path) -> dict[str, str]:
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT w.asset_ticker, w.scheduled_date "
            "FROM scheduled_event_window_observations w "
            "JOIN (SELECT asset_ticker, MAX(available_at) AS mx "
            "      FROM scheduled_event_window_observations GROUP BY asset_ticker) x "
            "ON w.asset_ticker=x.asset_ticker AND w.available_at=x.mx "
            "WHERE w.event_status='scheduled'"
        ).fetchall()
    return {str(r["asset_ticker"]): str(r["scheduled_date"]) for r in rows}


def coverage_audit(db_path: Path) -> dict[str, Any]:
    expected = {
        ("eps","average"), ("eps","high"), ("eps","low"),
        ("revenue","average"), ("revenue","high"), ("revenue","low"),
        ("analyst_count","count"),
    }
    with _conn(db_path) as conn:
        sources = conn.execute(
            "SELECT source_observation_id, asset_ticker, MAX(available_at) AS available_at "
            "FROM expectation_observations GROUP BY source_observation_id, asset_ticker "
            "ORDER BY available_at"
        ).fetchall()
        out = {}
        incomplete_total = 0
        for src in sources:
            rows = conn.execute(
                "SELECT metric_key, statistic_key, fiscal_period, metadata_json "
                "FROM expectation_observations WHERE source_observation_id=? AND asset_ticker=?",
                (src["source_observation_id"], src["asset_ticker"]),
            ).fetchall()
            groups: dict[tuple[str,str], set[tuple[str,str]]] = {}
            for r in rows:
                try:
                    meta = json.loads(r["metadata_json"] or "{}")
                except Exception:
                    meta = {}
                scope = str(meta.get("period_scope") or meta.get("provider_horizon") or "unknown")
                key = (scope, str(r["fiscal_period"] or ""))
                groups.setdefault(key, set()).add((str(r["metric_key"]), str(r["statistic_key"])))
            incomplete = {f"{k[0]}|{k[1]}": sorted(expected-v) for k,v in groups.items() if v != expected}
            incomplete_total += len(incomplete)
            out[str(src["asset_ticker"])] = {
                "rows": len(rows),
                "period_groups": len(groups),
                "complete_period_groups": len(groups)-len(incomplete),
                "incomplete_period_groups": len(incomplete),
                "latest_snapshot_available_at": str(src["available_at"]),
                "incomplete_examples": dict(list(incomplete.items())[:5]),
            }
    return {
        "status": "PASS" if incomplete_total == 0 else "PASS_WITH_PROVIDER_MISSING_FIELDS",
        "symbols": len(out),
        "incomplete_period_groups_total": incomplete_total,
        "per_symbol": out,
        "interpretation": (
            "Fewer complete provider periods for one symbol are coverage differences, not malformed rows. "
            "No padding or synthetic expectations are created."
        ),
    }

from __future__ import annotations

import csv
import json
import time
from dataclasses import dataclass
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

from ingestion.expectations.alphavantage_expectations_v001 import capture_estimates_for_symbol


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def read_symbols(symbols: str | None = None, symbols_file: str | None = None) -> list[str]:
    out: list[str] = []
    if symbols:
        out.extend(x.strip().upper() for x in symbols.split(",") if x.strip())
    if symbols_file:
        p = Path(symbols_file)
        text = p.read_text(encoding="utf-8")
        if p.suffix.lower() == ".json":
            obj = json.loads(text)
            if isinstance(obj, list):
                vals = obj
            elif isinstance(obj, dict):
                vals = obj.get("symbols") or obj.get("tickers") or obj.get("universe") or []
                if vals and isinstance(vals[0], dict):
                    vals = [x.get("ticker") or x.get("symbol") for x in vals]
            else:
                vals = []
            out.extend(str(x).strip().upper() for x in vals if x)
        elif p.suffix.lower() == ".csv":
            rows = list(csv.DictReader(text.splitlines()))
            for r in rows:
                v = r.get("ticker") or r.get("symbol") or next(iter(r.values()), None)
                if v:
                    out.append(str(v).strip().upper())
        else:
            out.extend(x.strip().upper() for x in text.splitlines() if x.strip() and not x.lstrip().startswith("#"))
    seen = set()
    clean = []
    for s in out:
        if s and s not in seen:
            seen.add(s); clean.append(s)
    return clean


def _latest_capture_by_symbol(db_path: Path) -> dict[str, datetime]:
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT asset_ticker, MAX(available_at) FROM expectation_observations "
            "WHERE asset_ticker IS NOT NULL GROUP BY asset_ticker"
        ).fetchall()
    finally:
        conn.close()
    out = {}
    for sym, ts in rows:
        if ts:
            out[str(sym)] = datetime.fromisoformat(str(ts).replace("Z", "+00:00"))
    return out


def _calendar_dates(db_path: Path) -> dict[str, date]:
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    try:
        rows = conn.execute(
            "SELECT raw_payload_json FROM source_observations WHERE source_ref='alpha_vantage:EARNINGS_CALENDAR' "
            "ORDER BY available_at DESC LIMIT 1"
        ).fetchall()
    finally:
        conn.close()
    if not rows:
        return {}
    try:
        payload = json.loads(rows[0][0] or "{}")
    except Exception:
        return {}
    result = {}
    for r in payload.get("parsed_rows", []):
        sym = str(r.get("symbol") or "").upper().strip()
        d = str(r.get("reportDate") or r.get("report_date") or "").strip()
        try:
            if sym and d:
                result[sym] = date.fromisoformat(d)
        except ValueError:
            pass
    return result


def plan_due_symbols(db_path: Path, symbols: list[str], cfg: dict[str, Any], now: datetime | None = None) -> dict[str, Any]:
    now = now or datetime.now(timezone.utc)
    latest = _latest_capture_by_symbol(db_path)
    calendar = _calendar_dates(db_path)
    cadence = cfg["cadence_policy"]
    deep = set(cadence.get("deep_cohort", []))
    candidates = []
    for sym in symbols:
        event_date = calendar.get(sym)
        days_to_event = (event_date - now.date()).days if event_date else None
        if sym in deep:
            interval = int(cadence["deep_cohort_days"]); reason = "deep_cohort"; priority = 0
        elif days_to_event is not None and 0 <= days_to_event <= 7:
            interval = int(cadence["earnings_within_7d_days"]); reason = "earnings_within_7d"; priority = 1
        elif days_to_event is not None and 0 <= days_to_event <= 30:
            interval = int(cadence["earnings_within_30d_days"]); reason = "earnings_within_30d"; priority = 2
        else:
            interval = int(cadence["broad_universe_days"]); reason = "broad_universe"; priority = 3
        last = latest.get(sym)
        age_days = (now - last).total_seconds() / 86400 if last else None
        due = last is None or age_days >= interval
        candidates.append({
            "symbol": sym, "due": due, "cadence_days": interval, "reason": reason,
            "priority": priority, "days_to_event": days_to_event,
            "last_capture_at": last.isoformat() if last else None,
            "age_days": age_days,
        })
    due = [x for x in candidates if x["due"]]
    due.sort(key=lambda x: (x["priority"], 99999 if x["days_to_event"] is None else abs(x["days_to_event"]), x["symbol"]))
    budget = int(cfg["request_policy"]["default_daily_request_budget"])
    selected = due[:budget]
    return {
        "status": "READY",
        "symbols_considered": len(symbols),
        "symbols_due": len(due),
        "request_budget": budget,
        "selected_count": len(selected),
        "selected": selected,
        "deferred_due_count": max(0, len(due)-len(selected)),
        "feature_visibility": cfg["feature_visibility"],
    }


def capture_selected(db_path: Path, api_key: str, selected: list[dict[str, Any]], cfg: dict[str, Any], run_day: str | None = None) -> dict[str, Any]:
    policy = cfg["request_policy"]
    checkpoint_dir = Path(policy["checkpoint_dir"])
    day = run_day or datetime.now(timezone.utc).date().isoformat()
    checkpoint_dir.mkdir(parents=True, exist_ok=True)
    cp = checkpoint_dir / f"capture_{day}.json"
    state = {"day": day, "completed": {}, "failures": {}, "started_at": utc_now_iso()}
    if cp.exists():
        try:
            old = json.loads(cp.read_text(encoding="utf-8"))
            if old.get("day") == day:
                state.update(old)
        except Exception:
            pass
    results = []
    min_interval = float(policy["minimum_seconds_between_requests"])
    timeout = int(policy["request_timeout_seconds"])
    todo = [x for x in selected if x["symbol"] not in state.get("completed", {})]
    for idx, item in enumerate(todo):
        if idx:
            time.sleep(max(0.0, min_interval))
        sym = item["symbol"]
        try:
            result = capture_estimates_for_symbol(db_path, api_key, sym, timeout=timeout)
            results.append(result)
            state.setdefault("completed", {})[sym] = result
            state.setdefault("failures", {}).pop(sym, None)
        except Exception as exc:
            state.setdefault("failures", {})[sym] = str(exc)
        state["updated_at"] = utc_now_iso()
        cp.write_text(json.dumps(state, indent=2, sort_keys=True), encoding="utf-8")
    status = "PASS" if not state.get("failures") else "PARTIAL"
    return {
        "status": status,
        "checkpoint": str(cp),
        "selected": len(selected),
        "completed_total": len(state.get("completed", {})),
        "failures": state.get("failures", {}),
        "results_this_run": results,
        "feature_visibility": cfg["feature_visibility"],
    }

from __future__ import annotations

import csv
import hashlib
import io
import json
import os
import re
import time
import urllib.parse
import urllib.request
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable

from ingestion.expectations.foundation_v001 import ingest_records

BASE_URL = "https://www.alphavantage.co/query"
SOURCE_NAME = "Alpha Vantage"
ADAPTER_VERSION = "alphavantage_expectations_v001"


def utc_now_iso() -> str:
    return datetime.now(timezone.utc).isoformat()


def _sha256_bytes(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _request(params: dict[str, str], api_key: str, timeout: int = 45) -> tuple[bytes, str, str]:
    request_params = dict(params)
    request_params["apikey"] = api_key
    url = BASE_URL + "?" + urllib.parse.urlencode(request_params)
    safe_url = BASE_URL + "?" + urllib.parse.urlencode(params)
    req = urllib.request.Request(url, headers={"User-Agent": "quant_market_ai research capture/1.0"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        body = resp.read()
        content_type = resp.headers.get("Content-Type", "")
    return body, content_type, safe_url


def _source_record(*, body: bytes, safe_url: str, retrieved_at: str, source_ref: str, payload: Any, metadata: dict[str, Any]) -> dict[str, Any]:
    return {
        "kind": "source_observation",
        "payload": {
            "source_type": "analyst_expectation_feed",
            "source_name": SOURCE_NAME,
            "source_ref": source_ref,
            "canonical_url": safe_url,
            "published_at": None,
            "first_seen_at": retrieved_at,
            "retrieved_at": retrieved_at,
            "available_at": retrieved_at,
            "strict_pit": 1,
            "content_sha256": _sha256_bytes(body),
            "raw_payload_json": payload,
            "metadata_json": {"adapter_version": ADAPTER_VERSION, **metadata},
        },
    }


def _float_or_none(value: Any) -> float | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text or text.lower() in {"none", "null", "nan", "n/a", "-"}:
        return None
    try:
        return float(text)
    except ValueError:
        return None


def parse_calendar_csv(text: str) -> list[dict[str, str]]:
    rows = list(csv.DictReader(io.StringIO(text)))
    return [{str(k): ("" if v is None else str(v)) for k, v in row.items()} for row in rows if row.get("symbol")]


def calendar_records(text: str, retrieved_at: str, safe_url: str) -> list[dict[str, Any]]:
    rows = parse_calendar_csv(text)
    body = text.encode("utf-8")
    source = _source_record(
        body=body,
        safe_url=safe_url,
        retrieved_at=retrieved_at,
        source_ref="alpha_vantage:EARNINGS_CALENDAR",
        payload={"raw_csv": text, "parsed_rows": rows},
        metadata={"response_format": "csv", "row_count": len(rows)},
    )
    # Create source ID using the foundation itself by ingesting source first; callers
    # use source-only capture here. Calendar rows deliberately do NOT become
    # scheduled_event_observations because reportDate/daypart are not exact timestamps.
    return [source]


def _find_estimate_rows(payload: dict[str, Any]) -> list[dict[str, Any]]:
    for key in ("estimates", "annualEstimates", "quarterlyEstimates", "data"):
        val = payload.get(key)
        if isinstance(val, list):
            return [x for x in val if isinstance(x, dict)]
    # Some provider versions may return multiple arrays. Collect any list of dicts.
    found: list[dict[str, Any]] = []
    for val in payload.values():
        if isinstance(val, list) and val and all(isinstance(x, dict) for x in val):
            found.extend(val)
    return found


def normalize_period_scope(horizon: Any) -> str:
    """Normalize provider horizon into a stable series discriminator.

    This is deliberately metadata-only so existing V001 capture rows remain immutable.
    Historical rows without period_scope are interpreted with the same function from
    provider_horizon by the V0012 quality/revision layer.
    """
    text = str(horizon or "").strip().lower().replace("_", " ")
    if "quarter" in text:
        return "fiscal_quarter"
    if "year" in text:
        return "fiscal_year"
    if not text:
        return "unknown"
    return "provider:" + re.sub(r"[^a-z0-9]+", "_", text).strip("_")


def _first(row: dict[str, Any], names: Iterable[str]) -> Any:
    for name in names:
        if name in row and row[name] not in (None, ""):
            return row[name]
    return None


def normalize_estimate_rows(symbol: str, payload: dict[str, Any], source_observation_id: str, retrieved_at: str) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for row in _find_estimate_rows(payload):
        fiscal = _first(row, ("fiscalDateEnding", "date", "fiscal_period"))
        horizon = _first(row, ("horizon", "period", "fiscalPeriod"))
        provider_as_of = _first(row, ("asOfDate", "as_of", "updatedAt"))
        # Provider fields vary across versions. We preserve exact field name in metadata.
        metric_fields = [
            ("eps", "average", ("eps_estimate_average", "estimatedEPS", "epsAverage", "epsAvg")),
            ("eps", "high", ("eps_estimate_high", "epsHigh")),
            ("eps", "low", ("eps_estimate_low", "epsLow")),
            ("revenue", "average", ("revenue_estimate_average", "revenueAverage", "revenueAvg")),
            ("revenue", "high", ("revenue_estimate_high", "revenueHigh")),
            ("revenue", "low", ("revenue_estimate_low", "revenueLow")),
            ("analyst_count", "count", ("eps_estimate_analyst_count", "analystCount", "numberAnalystsEstimatedEps")),
        ]
        for metric, statistic, aliases in metric_fields:
            raw = _first(row, aliases)
            value = _float_or_none(raw)
            if value is None:
                continue
            source_field = next((a for a in aliases if row.get(a) not in (None, "")), None)
            records.append({
                "kind": "expectation_observation",
                "payload": {
                    "entity_key": symbol,
                    "asset_ticker": symbol,
                    "expectation_type": "analyst_consensus",
                    "metric_key": metric,
                    "fiscal_period": str(fiscal or horizon or "unknown"),
                    "statistic_key": statistic,
                    "value_real": value,
                    "value_text": None,
                    "unit": "count" if metric == "analyst_count" else ("currency_per_share" if metric == "eps" else ("currency" if metric == "revenue" else None)),
                    "currency": str(row.get("currency")) if row.get("currency") not in (None, "") and metric in {"eps", "revenue"} else None,
                    "provider_as_of": provider_as_of if isinstance(provider_as_of, str) and "T" in provider_as_of else None,
                    "available_at": retrieved_at,
                    "strict_pit": 1,
                    "source_observation_id": source_observation_id,
                    "metadata_json": {
                        "adapter_version": ADAPTER_VERSION,
                        "provider_source_field": source_field,
                        "provider_horizon": horizon,
                        "period_scope": normalize_period_scope(horizon),
                        "provider_row": row,
                    },
                },
            })
    return records


def capture_calendar(db_path: Path, api_key: str, horizon: str = "3month", timeout: int = 45) -> dict[str, Any]:
    retrieved_at = utc_now_iso()
    params = {"function": "EARNINGS_CALENDAR", "horizon": horizon}
    body, content_type, safe_url = _request(params, api_key, timeout)
    text = body.decode("utf-8", errors="strict")
    rows = parse_calendar_csv(text)
    if not rows:
        raise RuntimeError("Alpha Vantage calendar returned no parseable rows")
    records = calendar_records(text, retrieved_at, safe_url)
    result = ingest_records(db_path, records)
    return {
        "status": "PASS_SOURCE_CAPTURED",
        "adapter_version": ADAPTER_VERSION,
        "endpoint": "EARNINGS_CALENDAR",
        "retrieved_at": retrieved_at,
        "rows": len(rows),
        "normalized_scheduled_events": 0,
        "normalization_note": "schedule normalization intentionally blocked: provider date/daypart is not an exact timestamp",
        "content_type": content_type,
        **result,
    }


def capture_estimates_for_symbol(db_path: Path, api_key: str, symbol: str, timeout: int = 45) -> dict[str, Any]:
    symbol = symbol.upper().strip()
    retrieved_at = utc_now_iso()
    params = {"function": "EARNINGS_ESTIMATES", "symbol": symbol}
    body, content_type, safe_url = _request(params, api_key, timeout)
    try:
        payload = json.loads(body.decode("utf-8"))
    except json.JSONDecodeError as exc:
        raise RuntimeError(f"Alpha Vantage estimates for {symbol} did not return JSON") from exc
    if not isinstance(payload, dict):
        raise RuntimeError("unexpected Alpha Vantage estimates payload")
    if any(k in payload for k in ("Note", "Information", "Error Message")):
        raise RuntimeError(f"Alpha Vantage response for {symbol}: {payload}")
    source = _source_record(
        body=body,
        safe_url=safe_url,
        retrieved_at=retrieved_at,
        source_ref=f"alpha_vantage:EARNINGS_ESTIMATES:{symbol}",
        payload=payload,
        metadata={"response_format": "json", "symbol": symbol},
    )
    source_result = ingest_records(db_path, [source])
    # Deterministic ID is generated inside foundation; reconstruct it using the same helper.
    from ingestion.expectations.foundation_v001 import deterministic_observation_id
    source_id = deterministic_observation_id("source_observation", source["payload"])
    normalized = normalize_estimate_rows(symbol, payload, source_id, retrieved_at)
    child_result = ingest_records(db_path, normalized) if normalized else {"inserted": 0, "skipped": 0}
    return {
        "status": "PASS_SOURCE_CAPTURED" if normalized else "PASS_SOURCE_ONLY_NORMALIZATION_EMPTY",
        "adapter_version": ADAPTER_VERSION,
        "endpoint": "EARNINGS_ESTIMATES",
        "symbol": symbol,
        "retrieved_at": retrieved_at,
        "normalized_expectations": len(normalized),
        "content_type": content_type,
        "source_inserted": source_result.get("inserted", 0),
        "expectation_inserted": child_result.get("inserted", 0),
        "expectation_skipped": child_result.get("skipped", 0),
    }


def capture_estimates_pilot(db_path: Path, api_key: str, symbols: list[str], timeout: int = 45, min_interval: float = 13.0) -> dict[str, Any]:
    results = []
    failures = []
    for idx, symbol in enumerate(symbols):
        if idx:
            time.sleep(max(0.0, min_interval))
        try:
            results.append(capture_estimates_for_symbol(db_path, api_key, symbol, timeout))
        except Exception as exc:  # capture pipeline records failures instead of fabricating data
            failures.append({"symbol": symbol, "error": str(exc)})
    return {
        "status": "PASS" if not failures else "PARTIAL",
        "symbols_requested": len(symbols),
        "symbols_captured": len(results),
        "failures": failures,
        "results": results,
    }


def api_key_from_env(env_name: str = "ALPHAVANTAGE_API_KEY") -> str:
    key = os.environ.get(env_name, "").strip()
    if not key:
        raise RuntimeError(f"missing API key environment variable: {env_name}")
    return key

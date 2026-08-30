#!/usr/bin/env python3
"""Audit extreme Temporal V002 outcomes from local lineage without mutating V002."""

from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "processed" / "market_temporal_v002.db"
DEFAULT_CONFIG = ROOT / "config" / "temporal_v002_tail_audit_v001.json"
DEFAULT_REVIEW = ROOT / "reports" / "market_temporal_v002_review" / "audit.json"
DEFAULT_REPORT_DIR = ROOT / "reports" / "market_temporal_v002_tail_audit_v001"


def _read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _sha256(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while chunk := handle.read(chunk_size):
            digest.update(chunk)
    return digest.hexdigest()


def _file_state(path: Path) -> dict[str, Any]:
    result: dict[str, Any] = {}
    for suffix in ("", "-wal", "-journal"):
        candidate = Path(str(path) + suffix)
        result[suffix or "main"] = (
            [candidate.stat().st_size, candidate.stat().st_mtime_ns]
            if candidate.exists()
            else None
        )
    return result


def _connect_ro(path: Path) -> sqlite3.Connection:
    conn = sqlite3.connect(f"file:{path.resolve()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _extreme_keys(report: dict[str, Any], audited_taus: set[int]) -> list[dict[str, Any]]:
    selected: dict[tuple[str, int], dict[str, Any]] = {}
    for horizon in report.get("horizons", []):
        tau = int(horizon.get("tau_sessions", -1))
        if tau not in audited_taus:
            continue
        extreme = horizon.get("extreme_rows") or {}
        for tail in ("lower", "upper"):
            for rank, item in enumerate(extreme.get(tail, []), start=1):
                key = (str(item["state_id"]), tau)
                record = dict(item)
                record["tail_memberships"] = sorted(
                    set(record.get("tail_memberships", [])) | {f"{tail}:{rank}"}
                )
                selected[key] = record
    return [selected[key] for key in sorted(selected)]


def _audit_one(
    conn: sqlite3.Connection,
    item: dict[str, Any],
    return_tolerance: float,
    log_tolerance: float,
    move_threshold: float,
) -> dict[str, Any]:
    state_id = str(item["state_id"])
    tau = int(item["tau_sessions"])
    row = conn.execute(
        """
        SELECT s.origin_id,s.asset_id,s.ticker,s.origin_trading_day,
               s.origin_session_index,s.provider_close_origin,
               o.target_trading_day,o.raw_close_return_pct,o.total_return_pct,
               o.cash_distribution_count,o.split_action_count,
               o.action_overlap_class,o.total_return_label_status,
               p.provider_close AS target_close
        FROM temporal_origins s
        JOIN temporal_outcomes o ON o.origin_id=s.origin_id AND o.tau_sessions=?
        LEFT JOIN temporal_price_points p
          ON p.asset_id=s.asset_id
         AND p.asset_session_index=s.origin_session_index+?
        WHERE s.state_id=?
        """,
        (tau, tau, state_id),
    ).fetchone()
    failures: list[str] = []
    if row is None:
        return {"state_id": state_id, "tau_sessions": tau, "failures": ["OUTCOME_MISSING"]}
    steps = conn.execute(
        """
        SELECT asset_session_index,trading_day,provider_close_previous,
               provider_close_current,cash_distribution,split_factor_product,
               cash_action_count,split_action_count,economic_gross_factor,
               log_economic_gross_factor,step_status
        FROM temporal_return_steps
        WHERE asset_id=? AND asset_session_index>? AND asset_session_index<=?
        ORDER BY asset_session_index
        """,
        (
            int(row["asset_id"]),
            int(row["origin_session_index"]),
            int(row["origin_session_index"]) + tau,
        ),
    ).fetchall()
    if len(steps) != tau:
        failures.append(f"PATH_LENGTH:{len(steps)}!={tau}")
    if not steps or str(steps[-1]["trading_day"]) != str(row["target_trading_day"]):
        failures.append("TARGET_DAY_MISMATCH")

    log_sum = 0.0
    direct_product = 1.0
    valid_path = True
    maximum_abs_move = 0.0
    large_moves: list[dict[str, Any]] = []
    for step in steps:
        previous = float(step["provider_close_previous"] or math.nan)
        current = float(step["provider_close_current"] or math.nan)
        factor = float(step["economic_gross_factor"] or math.nan)
        raw_log_factor = step["log_economic_gross_factor"]
        log_factor = float(raw_log_factor) if raw_log_factor is not None else math.nan
        if not all(math.isfinite(value) and value > 0.0 for value in (previous, current, factor)):
            failures.append(f"INVALID_STEP:{step['trading_day']}")
            valid_path = False
            continue
        if not math.isfinite(log_factor) or abs(math.log(factor) - log_factor) > log_tolerance:
            failures.append(f"LOG_FACTOR_IDENTITY:{step['trading_day']}")
            valid_path = False
            continue
        direct_product *= factor
        log_sum += log_factor
        raw_move = 100.0 * (current / previous - 1.0)
        maximum_abs_move = max(maximum_abs_move, abs(raw_move))
        if abs(raw_move) >= move_threshold:
            large_moves.append(
                {
                    "trading_day": str(step["trading_day"]),
                    "raw_close_move_pct": raw_move,
                    "cash_distribution": float(step["cash_distribution"]),
                    "split_factor_product": float(step["split_factor_product"]),
                    "step_status": str(step["step_status"]),
                }
            )

    origin_close = float(row["provider_close_origin"] or math.nan)
    target_close = float(row["target_close"] or math.nan)
    valid_endpoints = all(
        math.isfinite(value) and value > 0.0 for value in (origin_close, target_close)
    )
    if not valid_endpoints:
        failures.append("INVALID_ENDPOINT_PRICE")
    raw_direct = (
        100.0 * (target_close / origin_close - 1.0) if valid_endpoints else None
    )
    total_direct = 100.0 * (direct_product - 1.0) if valid_path else None
    total_prefix = 100.0 * math.expm1(log_sum) if valid_path else None
    raw_error = (
        abs(raw_direct - float(row["raw_close_return_pct"]))
        if raw_direct is not None
        else None
    )
    total_error = (
        abs(total_direct - float(row["total_return_pct"]))
        if total_direct is not None
        else None
    )
    prefix_error = (
        abs(total_prefix - float(row["total_return_pct"]))
        if total_prefix is not None
        else None
    )
    if raw_error is not None and raw_error > return_tolerance:
        failures.append("RAW_ENDPOINT_IDENTITY")
    if total_error is not None and total_error > return_tolerance:
        failures.append("TOTAL_PRODUCT_IDENTITY")
    if prefix_error is not None and prefix_error > return_tolerance:
        failures.append("TOTAL_PREFIX_IDENTITY")
    cash_count = sum(int(step["cash_action_count"]) for step in steps)
    split_count = sum(int(step["split_action_count"]) for step in steps)
    if cash_count != int(row["cash_distribution_count"]):
        failures.append("CASH_COUNT_IDENTITY")
    if split_count != int(row["split_action_count"]):
        failures.append("SPLIT_COUNT_IDENTITY")
    if str(row["total_return_label_status"]) != "usable":
        failures.append("OUTCOME_NOT_USABLE")

    return {
        "state_id": state_id,
        "asset_id": int(row["asset_id"]),
        "ticker": str(row["ticker"]),
        "origin_trading_day": str(row["origin_trading_day"]),
        "target_trading_day": str(row["target_trading_day"]),
        "tau_sessions": tau,
        "tail_memberships": item.get("tail_memberships", []),
        "stored_total_return_pct": float(row["total_return_pct"]),
        "stored_raw_close_return_pct": float(row["raw_close_return_pct"]),
        "action_overlap_class": str(row["action_overlap_class"]),
        "path_steps": len(steps),
        "cash_distribution_count": cash_count,
        "split_action_count": split_count,
        "maximum_absolute_daily_raw_move_pct": maximum_abs_move,
        "large_daily_moves": large_moves,
        "raw_endpoint_absolute_error_pct": raw_error,
        "economic_product_absolute_error_pct": total_error,
        "prefix_absolute_error_pct": prefix_error,
        "failures": failures,
    }


def build_report(
    db_path: Path,
    config_path: Path,
    review_path: Path,
    stage: str,
) -> dict[str, Any]:
    cfg = _read_json(config_path)
    distribution_path = ROOT / cfg["source_distribution_report"]
    distribution = _read_json(distribution_path)
    review = _read_json(review_path)
    before = _file_state(db_path)
    expected_sha = str(review.get("v002_dataset_sha256"))
    actual_sha = _sha256(db_path) if stage == "audit" else expected_sha
    failures: list[str] = []
    if distribution.get("status") != "PASS":
        failures.append("SOURCE_DISTRIBUTION_REVIEW_NOT_PASS")
    if review.get("target_distribution_status") != "PASS":
        failures.append("SOURCE_REVIEW_TARGET_DISTRIBUTION_NOT_PASS")
    if actual_sha != expected_sha:
        failures.append("V002_SHA256_MISMATCH")
    keys = _extreme_keys(distribution, {int(x) for x in cfg["audited_taus"]})
    audits: list[dict[str, Any]] = []
    if stage == "audit":
        with _connect_ro(db_path) as conn:
            audits = [
                _audit_one(
                    conn,
                    item,
                    float(cfg["return_absolute_tolerance_pct"]),
                    float(cfg["log_factor_absolute_tolerance"]),
                    float(cfg["large_daily_move_diagnostic_pct"]),
                )
                for item in keys
            ]
        for item in audits:
            failures.extend(
                f"{item['state_id']}|{item['tau_sessions']}|{failure}"
                for failure in item.get("failures", [])
            )
    after = _file_state(db_path)
    if before != after:
        failures.append("V002_CHANGED_DURING_TAIL_AUDIT")
    ticker_counts = Counter(item.get("ticker") for item in audits)
    tau_counts = Counter(int(item.get("tau_sessions", -1)) for item in audits)
    return {
        "version": cfg["version"],
        "stage": stage,
        "status": "PASS" if not failures else "FAIL",
        "training_authorized": False,
        "v002_db": str(db_path.resolve()),
        "v002_opened_read_only": True,
        "v002_stable_during_audit": before == after,
        "v002_dataset_sha256": actual_sha,
        "source_review_status": review.get("status"),
        "source_distribution_status": distribution.get("status"),
        "audited_unique_outcomes": len(audits) if stage == "audit" else len(keys),
        "audited_taus": sorted(tau_counts) if audits else cfg["audited_taus"],
        "outcomes_by_tau": {str(k): v for k, v in sorted(tau_counts.items())},
        "outcomes_by_ticker": {str(k): v for k, v in sorted(ticker_counts.items())},
        "maximum_absolute_daily_raw_move_pct": (
            max((float(item["maximum_absolute_daily_raw_move_pct"]) for item in audits), default=None)
        ),
        "no_plausibility_clipping_performed": True,
        "failures": failures,
        "outcomes": audits,
        "v009_loaded_or_modified": False,
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("plan", "audit"), required=True)
    parser.add_argument("--v002-db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--review", type=Path, default=DEFAULT_REVIEW)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    args = parser.parse_args()
    payload = build_report(args.v002_db, args.config, args.review, args.stage)
    _write_json(args.report_dir / f"{args.stage}.json", payload)
    print(json.dumps({k: payload[k] for k in ("version", "stage", "status", "audited_unique_outcomes", "failures")}, indent=2))
    raise SystemExit(0 if payload["status"] == "PASS" else 1)


if __name__ == "__main__":
    main()

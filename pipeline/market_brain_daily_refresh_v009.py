from __future__ import annotations

import argparse
import json
import math
import os
import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from evaluation.market.daily_v003_core_audit import audit as audit_core
from features.market.daily_v003_core import (
    DEFAULT_CONFIG as CORE_CONFIG,
    build as build_core_database,
)
from ingestion.prices.yahoo_daily_broad_v003 import quality_status
from ingestion.prices.yahoo_daily_refresh_v009 import run_asset_refresh
from ingestion.prices.yahoo_daily_v1 import ExchangeCalendarResolver
from ingestion.prices.yahoo_daily_v1 import (
    RawPriceStore,
    SOURCE_ID,
    canonical_json as price_canonical_json,
    persist_provider_frame,
)
from models.market.distributional_v009_prospective import (
    dataframe_sha256,
    file_sha256,
    load_artifact,
    load_config as load_v009_config,
    load_training_frame,
    sha256_json,
)
from storage.prospective_registry import connect_registry

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "market_brain_daily_refresh_v009.json"
DEFAULT_REPORT_ROOT = (
    ROOT / "reports" / "market_brain_distributional_v009"
    / "prospective_holdout_v001" / "daily_refresh_v002"
)
OHLC_REPAIR_REPORT = "ohlc_envelope_repair_v001.json"
OHLC_REPAIR_CHECKPOINT = "ohlc_envelope_repair_checkpoint_v001.json"


def utc_now() -> datetime:
    return datetime.now(timezone.utc)


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def write_json(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )
    temporary.replace(path)


def root_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    cfg = read_json(path)
    if cfg["version"] != "market_brain_daily_refresh_v009_v002":
        raise ValueError("unexpected refresh version")
    if cfg["supported_experiment_version"] != (
        "market_brain_distributional_v009_prospective_holdout_v001"
    ):
        raise ValueError("refresh is not bound to V009")
    if cfg["market_feature_version"] != "market_daily_state_v003_core":
        raise ValueError("refresh feature version changed")
    if cfg["label_version"] != "market_daily_reaction_v003_core":
        raise ValueError("refresh label version changed")
    if cfg["source_asof_contract"] != "daily_price_asof_v2":
        raise ValueError("refresh source as-of contract changed")
    if cfg["regular_market_close_fallback_version"] != (
        "regular_market_price_fallback_v001"
    ):
        raise ValueError("refresh close fallback changed")
    if cfg.get("ohlc_envelope_repair_version") != (
        "ohlc_envelope_repair_v001"
    ):
        raise ValueError("unexpected OHLC envelope repair version")
    if cfg.get("ohlc_envelope_repair_high_rule") != (
        "max(open,high,low,close)"
    ):
        raise ValueError("OHLC repair high rule changed")
    if cfg.get("ohlc_envelope_repair_low_rule") != (
        "min(open,high,low,close)"
    ):
        raise ValueError("OHLC repair low rule changed")
    if cfg.get("ohlc_envelope_repair_preserved_fields") != [
        "open", "close", "volume", "adjusted_close"
    ]:
        raise ValueError("OHLC repair preserved fields changed")
    if cfg.get(
        "ohlc_envelope_repair_requires_only_invalid_ohlc_failure"
    ) is not True:
        raise ValueError("OHLC repair failure-domain gate changed")
    if int(cfg.get("ohlc_envelope_repair_minimum_invalid_observations", 0)) < 2:
        raise ValueError("OHLC repair requires at least two observations")
    maximum_expansion = float(
        cfg.get("ohlc_envelope_repair_maximum_relative_expansion_pct", 0)
    )
    if not 0 < maximum_expansion <= 2.0:
        raise ValueError("OHLC repair expansion cap was relaxed")
    if int(cfg["maximum_incremental_request_calendar_days"]) > 31:
        raise ValueError("refresh may not become a historical backfill")
    if min(
        int(cfg["minimum_source_assets_on_origin"]),
        int(cfg["minimum_core_states_on_origin"]),
    ) < 490:
        raise ValueError("refresh coverage gate was relaxed")
    if cfg.get("refit_v009_after_refresh") is not False:
        raise ValueError("V009 refit after refresh is forbidden")
    return cfg


def frozen_contract(cfg: dict[str, Any]):
    prereg = read_json(root_path(cfg["fixed_preregistration"]))
    universe = read_json(root_path(cfg["fixed_universe_manifest"]))
    v009 = load_v009_config(root_path(cfg["v009_config"]))
    if prereg.get("status") != "PASS":
        raise RuntimeError("V009 preregistration is not PASS")
    if prereg.get("benchmark_version") != cfg["supported_experiment_version"]:
        raise RuntimeError("refresh/V009 version mismatch")
    if sha256_json(universe) != prereg.get("universe_manifest_sha256"):
        raise RuntimeError("frozen V009 universe changed")
    if v009["version"] != cfg["supported_experiment_version"]:
        raise RuntimeError("refresh/V009 config mismatch")
    return prereg, universe, v009


def frozen_fit(registry_db: Path, version: str) -> dict[str, Any]:
    with connect_registry(registry_db) as conn:
        row = conn.execute(
            "SELECT * FROM prospective_model_fits WHERE experiment_version=?",
            (version,),
        ).fetchone()
    if row is None:
        raise RuntimeError("V009 frozen fit is missing")
    fit = dict(row)
    artifact = Path(fit["artifact_path"])
    if not artifact.is_file():
        raise RuntimeError("V009 artifact is missing")
    if file_sha256(artifact) != fit["artifact_sha256"]:
        raise RuntimeError("V009 artifact hash changed")
    return fit


def source_rows(source_db: Path, asset_ids: list[int]):
    marks = ",".join("?" for _ in asset_ids)
    with sqlite3.connect(source_db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            f"""
            WITH coverage AS (
              SELECT asset_id,MAX(trading_day) last_day
              FROM daily_price_quality_gated_observations_v002
              WHERE asset_id IN ({marks}) GROUP BY asset_id
            ), raw AS (
              SELECT asset_id,provider_symbol,exchange,retrieved_at,
                     ROW_NUMBER() OVER(
                       PARTITION BY asset_id
                       ORDER BY julianday(retrieved_at) DESC,raw_batch_id DESC
                     ) rank
              FROM raw_price_batches
              WHERE asset_id IN ({marks}) AND interval='1d'
            )
            SELECT a.asset_id,a.ticker,a.exchange,c.last_day,
                   r.provider_symbol,r.exchange raw_exchange,r.retrieved_at
            FROM assets a
            LEFT JOIN coverage c ON c.asset_id=a.asset_id
            LEFT JOIN raw r ON r.asset_id=a.asset_id AND r.rank=1
            WHERE a.asset_id IN ({marks}) ORDER BY a.asset_id
            """,
            [*asset_ids, *asset_ids, *asset_ids],
        ).fetchall()
    return [dict(row) for row in rows]


def origin_clock(
    origin_day: str,
    exchanges: set[str],
    settlement_minutes: int,
    now: datetime,
):
    closes = []
    calendars = {}
    for exchange in sorted(exchanges):
        bounds = ExchangeCalendarResolver(
            exchange, start=origin_day, end=origin_day
        ).bounds(origin_day)
        if bounds is None:
            raise RuntimeError(
                f"{origin_day} is not a session for {exchange}"
            )
        close = bounds.close_utc.astimezone(timezone.utc)
        closes.append(close)
        calendars[exchange] = {
            "calendar": bounds.calendar_name,
            "close_utc": close.isoformat(),
        }
    earliest = max(closes) + timedelta(minutes=settlement_minutes)
    return {
        "calendars": calendars,
        "latest_close_utc": max(closes).isoformat(),
        "earliest_acquire_utc": earliest.isoformat(),
        "checked_at_utc": now.astimezone(timezone.utc).isoformat(),
        "status": (
            "READY"
            if now.astimezone(timezone.utc) >= earliest
            else "WAITING_FOR_CLOSE"
        ),
    }


def refresh_plan(
    config_path: Path,
    source_db: Path,
    registry_db: Path,
    origin_day: str,
    now: datetime | None = None,
):
    cfg = load_config(config_path)
    _, universe, v009 = frozen_contract(cfg)
    if origin_day < cfg["not_before_origin_day"]:
        raise RuntimeError("refresh origin predates frozen V009 start")
    fit = frozen_fit(registry_db, v009["version"])
    asset_ids = [int(row["asset_id"]) for row in universe["assets"]]
    rows = source_rows(source_db, asset_ids)
    if len(rows) != len(asset_ids):
        raise RuntimeError("source identities do not match frozen universe")
    end = (date.fromisoformat(origin_day) + timedelta(days=1)).isoformat()
    maximum = int(cfg["maximum_incremental_request_calendar_days"])
    assets, failures, exchanges = [], [], set()
    for row in rows:
        exchange = str(row["raw_exchange"] or row["exchange"] or "")
        last_day = row["last_day"]
        status, start = "ALREADY_PRESENT", None
        if not exchange:
            failures.append(f"missing exchange for {row['ticker']}")
        else:
            exchanges.add(exchange)
        if last_day is None:
            failures.append(f"no existing history for {row['ticker']}")
            status = "REQUIRES_BACKFILL"
        elif str(last_day) < origin_day:
            start = (
                date.fromisoformat(str(last_day)) + timedelta(days=1)
            ).isoformat()
            span = (
                date.fromisoformat(end) - date.fromisoformat(start)
            ).days
            status = "PENDING"
            if span > maximum:
                failures.append(f"{row['ticker']} is {span} days stale")
                status = "TOO_STALE"
        assets.append({
            "asset_id": int(row["asset_id"]),
            "ticker": str(row["ticker"]),
            "provider_symbol": str(
                row["provider_symbol"] or row["ticker"]
            ),
            "exchange": exchange,
            "source_last_day": last_day,
            "requested_start": start,
            "requested_end_exclusive": end if start else None,
            "status": status,
        })
    clock = origin_clock(
        origin_day,
        exchanges,
        int(cfg["provider_settlement_minutes_after_close"]),
        now or utc_now(),
    )
    return {
        "status": "FAIL" if failures else clock["status"],
        "refresh_version": cfg["version"],
        "experiment_version": v009["version"],
        "origin_day": origin_day,
        "fixed_fit_id": fit["fit_id"],
        "fixed_artifact_sha256": fit["artifact_sha256"],
        "frozen_universe_assets": len(asset_ids),
        "already_present": sum(
            row["status"] == "ALREADY_PRESENT" for row in assets
        ),
        "pending": sum(row["status"] == "PENDING" for row in assets),
        "clock": clock,
        "assets": assets,
        "failures": failures,
        "refit_v009_after_refresh": False,
    }


def audit_source(
    config_path: Path,
    source_db: Path,
    origin_day: str,
):
    cfg = load_config(config_path)
    _, universe, _ = frozen_contract(cfg)
    ids = [int(row["asset_id"]) for row in universe["assets"]]
    marks = ",".join("?" for _ in ids)
    with sqlite3.connect(source_db) as conn:
        rows = conn.execute(
            f"""
            SELECT asset_id,MIN(observed_at),MAX(observed_at)
            FROM daily_price_quality_gated_observations_v002
            WHERE trading_day=? AND asset_id IN ({marks})
            GROUP BY asset_id ORDER BY asset_id
            """,
            [origin_day, *ids],
        ).fetchall()
    present = {int(row[0]) for row in rows}
    minimum = int(cfg["minimum_source_assets_on_origin"])
    return {
        "status": "PASS" if len(present) >= minimum else "WAITING",
        "origin_day": origin_day,
        "source_assets": len(present),
        "minimum_required": minimum,
        "missing_asset_ids": sorted(set(ids) - present),
        "observation_clock": [
            {
                "asset_id": int(row[0]),
                "first_observed_at": row[1],
                "last_observed_at": row[2],
            }
            for row in rows
        ],
    }


def ohlc_envelope_repair(
    opened: float,
    high: float,
    low: float,
    closed: float,
    maximum_relative_expansion_pct: float,
) -> dict[str, float]:
    values = [float(opened), float(high), float(low), float(closed)]
    if any(not math.isfinite(value) or value <= 0 for value in values):
        raise ValueError("OHLC repair requires finite positive values")
    repaired_high = max(values)
    repaired_low = min(values)
    if repaired_high == float(high) and repaired_low == float(low):
        raise ValueError("OHLC row is already a valid envelope")
    upper_expansion = max(0.0, repaired_high - float(high))
    lower_expansion = max(0.0, float(low) - repaired_low)
    relative_expansion = (
        100.0 * max(upper_expansion, lower_expansion) / abs(float(closed))
    )
    if relative_expansion > float(maximum_relative_expansion_pct):
        raise ValueError(
            "OHLC repair exceeds frozen relative expansion cap: "
            f"{relative_expansion:.9f}%"
        )
    return {
        "open": float(opened),
        "original_high": float(high),
        "original_low": float(low),
        "close": float(closed),
        "repaired_high": repaired_high,
        "repaired_low": repaired_low,
        "upper_expansion": upper_expansion,
        "lower_expansion": lower_expansion,
        "relative_expansion_pct": relative_expansion,
    }


def _repair_policy(cfg: dict[str, Any]) -> dict[str, Any]:
    return {
        "version": cfg["ohlc_envelope_repair_version"],
        "effective_not_before": cfg[
            "ohlc_envelope_repair_effective_not_before"
        ],
        "minimum_invalid_observations": int(
            cfg["ohlc_envelope_repair_minimum_invalid_observations"]
        ),
        "maximum_relative_expansion_pct": float(
            cfg["ohlc_envelope_repair_maximum_relative_expansion_pct"]
        ),
        "high_rule": cfg["ohlc_envelope_repair_high_rule"],
        "low_rule": cfg["ohlc_envelope_repair_low_rule"],
        "preserved_fields": list(
            cfg["ohlc_envelope_repair_preserved_fields"]
        ),
        "requires_only_failed_check": "invalid_ohlc_rows",
        "application_scope": (
            "all checkpoint MISSING_ORIGIN rows satisfying the frozen rule; "
            "never a coverage-selected subset"
        ),
        "target_fields_changed": False,
        "model_refit": False,
    }


def _latest_origin_bar(
    conn: sqlite3.Connection,
    asset_id: int,
    origin_day: str,
) -> dict[str, Any]:
    conn.row_factory = sqlite3.Row
    row = conn.execute(
        """
        SELECT a.ticker,v.provider_symbol,v.exchange,v.trading_day,
               v.open,v.high,v.low,v.close,v.volume,v.adjusted_close,
               v.bar_content_sha256,o.price_observation_id,
               o.raw_batch_id,o.batch_retrieval_id,o.observed_at,
               o.observation_sequence
        FROM price_bar_observations o
        JOIN price_bar_versions v
          ON v.price_bar_version_id=o.price_bar_version_id
        JOIN assets a ON a.asset_id=v.asset_id
        WHERE o.source_id=? AND o.asset_id=? AND o.interval='1d'
          AND o.trading_day=?
        ORDER BY o.observation_sequence DESC LIMIT 1
        """,
        (SOURCE_ID, asset_id, origin_day),
    ).fetchone()
    if row is None:
        raise RuntimeError(f"missing origin observation for asset {asset_id}")
    invalid_observations = conn.execute(
        """
        SELECT COUNT(*)
        FROM price_bar_observations o
        JOIN price_bar_versions v
          ON v.price_bar_version_id=o.price_bar_version_id
        WHERE o.source_id=? AND o.asset_id=? AND o.interval='1d'
          AND o.trading_day=?
          AND (
            v.high < MAX(v.open,v.low,v.close)
            OR v.low > MIN(v.open,v.high,v.close)
          )
        """,
        (SOURCE_ID, asset_id, origin_day),
    ).fetchone()[0]
    payload = dict(row)
    payload["invalid_observations"] = int(invalid_observations)
    return payload


def _write_immutable_json(path: Path, payload: Any) -> None:
    text = json.dumps(
        payload, indent=2, sort_keys=True, allow_nan=False
    ) + "\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        if path.read_text(encoding="utf-8") != text:
            raise RuntimeError(f"refusing to overwrite immutable {path}")
        return
    path.write_text(text, encoding="utf-8")


def validate_ohlc_repair_gate(
    config_path: Path,
    source_db: Path,
    registry_db: Path,
    report_root: Path,
    origin_day: str,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    if origin_day < cfg["ohlc_envelope_repair_effective_not_before"]:
        return {"status": "NOT_REQUIRED", "origin_day": origin_day}
    report_path = report_root / origin_day / OHLC_REPAIR_REPORT
    if not report_path.is_file():
        return {
            "status": "MISSING_REQUIRED_OPERATIONAL_AMENDMENT",
            "origin_day": origin_day,
            "report_path": str(report_path),
        }
    report = read_json(report_path)
    failures: list[str] = []
    if report.get("status") != "PASS_OPERATIONAL_AMENDMENT":
        failures.append("repair report is not PASS")
    policy = _repair_policy(cfg)
    if report.get("policy") != policy:
        failures.append("repair policy changed")
    if report.get("policy_sha256") != sha256_json(policy):
        failures.append("repair policy hash mismatch")
    if report.get("origin_day") != origin_day:
        failures.append("repair origin mismatch")
    rows = list(report.get("repairs", []))
    observation_ids = [str(row["repaired_observation_id"]) for row in rows]
    if len(observation_ids) != len(set(observation_ids)):
        failures.append("duplicate repaired observation IDs")
    for row in rows:
        evidence = row["envelope"]
        try:
            expected = ohlc_envelope_repair(
                evidence["open"],
                evidence["original_high"],
                evidence["original_low"],
                evidence["close"],
                policy["maximum_relative_expansion_pct"],
            )
        except Exception as error:
            failures.append(str(error))
            continue
        if expected != evidence:
            failures.append(f"repair identity changed for {row['ticker']}")
    if observation_ids:
        marks = ",".join("?" for _ in observation_ids)
        with sqlite3.connect(source_db) as conn:
            eligible = conn.execute(
                f"""
                SELECT COUNT(DISTINCT price_observation_id)
                FROM daily_price_quality_gated_observations_v002
                WHERE trading_day=? AND price_observation_id IN ({marks})
                """,
                [origin_day, *observation_ids],
            ).fetchone()[0]
        if int(eligible) != len(observation_ids):
            failures.append("repaired observations are not quality eligible")
    source = audit_source(config_path, source_db, origin_day)
    if source["status"] != "PASS":
        failures.append("source coverage is not PASS after repair")
    return {
        "status": "PASS" if not failures else "FAIL",
        "origin_day": origin_day,
        "report_path": str(report_path),
        "report_sha256": file_sha256(report_path),
        "repair_count": len(rows),
        "source_assets": source["source_assets"],
        "failures": failures,
    }


def repair_ohlc_origin(
    config_path: Path,
    source_db: Path,
    core_db: Path,
    registry_db: Path,
    raw_root: Path,
    report_root: Path,
    origin_day: str,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    policy = _repair_policy(cfg)
    if origin_day < policy["effective_not_before"]:
        raise RuntimeError("OHLC repair predates its operational amendment")
    report_path = report_root / origin_day / OHLC_REPAIR_REPORT
    if report_path.is_file():
        gate = validate_ohlc_repair_gate(
            config_path, source_db, registry_db, report_root, origin_day
        )
        if gate["status"] != "PASS":
            raise RuntimeError("existing OHLC repair report failed validation")
        return {"status": "ALREADY_APPLIED", **gate}

    prereg, universe, v009 = frozen_contract(cfg)
    fit = frozen_fit(registry_db, v009["version"])
    with connect_registry(registry_db) as conn:
        sealed_before = int(conn.execute(
            "SELECT COUNT(*) FROM prospective_prediction_batches "
            "WHERE experiment_version=?",
            (v009["version"],),
        ).fetchone()[0])
    effective_day = str(policy["effective_not_before"])
    policy_anchor_path = report_root / effective_day / OHLC_REPAIR_REPORT
    if origin_day == effective_day:
        if sealed_before != 0:
            raise RuntimeError(
                "OHLC operational amendment must be frozen before first seal"
            )
        policy_anchor_sha256 = None
    else:
        if not policy_anchor_path.is_file():
            raise RuntimeError("pre-first-seal OHLC policy anchor is missing")
        policy_anchor = read_json(policy_anchor_path)
        if (
            policy_anchor.get("policy_sha256") != sha256_json(policy)
            or policy_anchor.get("sealed_batches_before_amendment") != 0
            or policy_anchor.get("performance_observed_before_amendment")
            is not False
        ):
            raise RuntimeError("pre-first-seal OHLC policy anchor changed")
        policy_anchor_sha256 = file_sha256(policy_anchor_path)
    observed_h1 = 0
    if core_db.is_file():
        with sqlite3.connect(core_db) as conn:
            observed_h1 = int(conn.execute(
                "SELECT COUNT(*) FROM market_daily_v003_labels "
                "WHERE origin_trading_day=? AND horizon_sessions=1 "
                "AND label_status <> 'insufficient_future'",
                (origin_day,),
            ).fetchone()[0])
    if observed_h1 != 0:
        raise RuntimeError("origin H1 outcome exists before OHLC amendment")

    acquisition_path = report_root / origin_day / "acquisition_checkpoint.json"
    if not acquisition_path.is_file():
        raise RuntimeError("acquisition checkpoint is required before repair")
    acquisition = read_json(acquisition_path)
    missing_rows = sorted(
        (
            row for row in acquisition.get("rows", {}).values()
            if row.get("status") == "MISSING_ORIGIN"
        ),
        key=lambda row: int(row["asset_id"]),
    )
    frozen_ids = {int(row["asset_id"]) for row in universe["assets"]}
    if any(int(row["asset_id"]) not in frozen_ids for row in missing_rows):
        raise RuntimeError("repair candidate is outside frozen universe")

    checkpoint_path = report_root / origin_day / OHLC_REPAIR_CHECKPOINT
    checkpoint = read_json(checkpoint_path) if checkpoint_path.is_file() else {
        "version": policy["version"],
        "origin_day": origin_day,
        "policy_sha256": sha256_json(policy),
        "policy_anchor_origin_day": effective_day,
        "policy_anchor_report_sha256": policy_anchor_sha256,
        "rows": {},
    }
    if (
        checkpoint.get("version") != policy["version"]
        or checkpoint.get("origin_day") != origin_day
        or checkpoint.get("policy_sha256") != sha256_json(policy)
    ):
        raise RuntimeError("OHLC repair checkpoint contract mismatch")

    for candidate in missing_rows:
        ticker = str(candidate["ticker"])
        failed_checks = {
            str(row["check_name"])
            for row in candidate.get("quality", {}).get("failed_checks", [])
        }
        if failed_checks != {"invalid_ohlc_rows"}:
            raise RuntimeError(
                f"{ticker} is outside the frozen OHLC repair domain: "
                f"{sorted(failed_checks)}"
            )
        prior_repair = checkpoint["rows"].get(ticker)
        if prior_repair and prior_repair.get("status") == "COMPLETED":
            acquisition["rows"][ticker] = dict(
                prior_repair["acquisition_row"]
            )
            continue
        with sqlite3.connect(source_db) as conn:
            original = _latest_origin_bar(
                conn, int(candidate["asset_id"]), origin_day
            )
        if original["invalid_observations"] < policy[
            "minimum_invalid_observations"
        ]:
            raise RuntimeError(
                f"{ticker} has insufficient repeated invalid observations"
            )
        envelope = ohlc_envelope_repair(
            original["open"], original["high"], original["low"],
            original["close"], policy["maximum_relative_expansion_pct"],
        )
        retrieved_at = utc_now().isoformat()
        derivation = {
            "version": policy["version"],
            "derivation_kind": "derived_operational_repair",
            "policy_sha256": sha256_json(policy),
            "source_price_observation_id": original[
                "price_observation_id"
            ],
            "source_raw_batch_id": original["raw_batch_id"],
            "source_bar_content_sha256": original[
                "bar_content_sha256"
            ],
            "source_observed_at": original["observed_at"],
            "envelope": envelope,
        }
        frame = pd.DataFrame(
            {
                "Open": [original["open"]],
                "High": [envelope["repaired_high"]],
                "Low": [envelope["repaired_low"]],
                "Close": [original["close"]],
                "Adj Close": [original["adjusted_close"]],
                "Volume": [original["volume"]],
                "Original High": [original["high"]],
                "Original Low": [original["low"]],
                "Repair Version": [policy["version"]],
                "Source Observation ID": [original[
                    "price_observation_id"
                ]],
            },
            index=pd.DatetimeIndex(
                [pd.Timestamp(origin_day, tz="America/New_York")],
                name="Date",
            ),
        )
        run_id = str(uuid.uuid4())
        with sqlite3.connect(source_db) as conn:
            conn.execute("PRAGMA foreign_keys=ON")
            conn.execute(
                """
                INSERT INTO source_ingestion_runs(
                  run_id,source_id,mode,started_at,status,
                  checkpoint_before_json
                ) VALUES (?,?,?,?,'running',?)
                """,
                (
                    run_id, SOURCE_ID, policy["version"], retrieved_at,
                    price_canonical_json(derivation),
                ),
            )
            conn.commit()
            try:
                persisted = persist_provider_frame(
                    conn,
                    RawPriceStore(raw_root),
                    asset_id=int(candidate["asset_id"]),
                    symbol=str(candidate["provider_symbol"]),
                    exchange=str(candidate["exchange"]),
                    requested_start=origin_day,
                    requested_end=(
                        date.fromisoformat(origin_day) + timedelta(days=1)
                    ).isoformat(),
                    retrieved_at=retrieved_at,
                    provider_library_version=policy["version"],
                    frame=frame,
                    source_run_id=run_id,
                    # Migration 013 freezes this column to the sole accepted
                    # value. The semantic derivation is explicit in the raw
                    # payload/request and provider_library_name instead.
                    lineage_kind="provider_library_output",
                    provider_library_name="quant_market_ai",
                    derivation=derivation,
                )
                quality = quality_status(source_db, persisted.quality_run_id)
                repaired_observation = conn.execute(
                    """
                    SELECT price_observation_id
                    FROM price_bar_observations
                    WHERE batch_retrieval_id=? AND asset_id=?
                      AND trading_day=?
                    """,
                    (
                        persisted.batch_retrieval_id,
                        int(candidate["asset_id"]), origin_day,
                    ),
                ).fetchone()
                if quality["status"] != "PASS" or repaired_observation is None:
                    raise RuntimeError(f"repaired quality failed for {ticker}")
                repaired_observation_id = str(repaired_observation[0])
                completed_at = utc_now().isoformat()
                conn.execute(
                    """
                    UPDATE source_ingestion_runs
                    SET finished_at=?,status='completed',
                        checkpoint_after_json=?,documents_discovered=1,
                        documents_inserted=1,documents_existing=0
                    WHERE run_id=?
                    """,
                    (
                        completed_at,
                        price_canonical_json({
                            "repaired_observation_id": repaired_observation_id,
                            "quality_run_id": persisted.quality_run_id,
                            "raw_batch_id": persisted.raw_batch_id,
                        }),
                        run_id,
                    ),
                )
                conn.commit()
            except BaseException as error:
                conn.rollback()
                conn.execute(
                    """
                    UPDATE source_ingestion_runs
                    SET finished_at=?,status='failed',error_count=1,
                        error_json=? WHERE run_id=?
                    """,
                    (
                        utc_now().isoformat(),
                        price_canonical_json({
                            "error_type": type(error).__name__,
                            "message": str(error),
                        }),
                        run_id,
                    ),
                )
                conn.commit()
                raise
        acquisition_row = {
            **candidate,
            "status": "COMPLETED",
            "origin_present": True,
            "quality": quality,
            "quality_run_id": persisted.quality_run_id,
            "run_id": run_id,
            "retrieved_at_utc": retrieved_at,
            "repair_version": policy["version"],
            "repaired_observation_id": repaired_observation_id,
            "source_price_observation_id": original[
                "price_observation_id"
            ],
        }
        checkpoint["rows"][ticker] = {
            "status": "COMPLETED",
            "ticker": ticker,
            "asset_id": int(candidate["asset_id"]),
            "source_price_observation_id": original[
                "price_observation_id"
            ],
            "source_raw_batch_id": original["raw_batch_id"],
            "source_bar_content_sha256": original[
                "bar_content_sha256"
            ],
            "repaired_observation_id": repaired_observation_id,
            "repaired_raw_batch_id": persisted.raw_batch_id,
            "repaired_quality_run_id": persisted.quality_run_id,
            "envelope": envelope,
            "acquisition_row": acquisition_row,
        }
        acquisition["rows"][ticker] = acquisition_row
        write_json(checkpoint_path, checkpoint)
        write_json(acquisition_path, acquisition)

    write_json(checkpoint_path, checkpoint)
    write_json(acquisition_path, acquisition)
    source = audit_source(config_path, source_db, origin_day)
    if source["status"] != "PASS":
        raise RuntimeError("source coverage did not PASS after OHLC repair")
    repairs = [
        {
            key: value for key, value in row.items()
            if key != "acquisition_row"
        }
        for _, row in sorted(checkpoint["rows"].items())
        if row.get("status") == "COMPLETED"
    ]
    report = {
        "status": "PASS_OPERATIONAL_AMENDMENT",
        "version": policy["version"],
        "origin_day": origin_day,
        "created_at_utc": utc_now().isoformat(),
        "policy": policy,
        "policy_sha256": sha256_json(policy),
        "repairs": repairs,
        "repair_count": len(repairs),
        "source_audit": source,
        "fixed_fit_id": fit["fit_id"],
        "fixed_artifact_sha256": fit["artifact_sha256"],
        "v009_config_sha256": file_sha256(root_path(cfg["v009_config"])),
        "feature_manifest_sha256": prereg["feature_manifest_sha256"],
        "sealed_batches_before_amendment": sealed_before,
        "observed_h1_outcomes_before_amendment": observed_h1,
        "performance_observed_before_amendment": (
            False if origin_day == effective_day else None
        ),
        "fit_changed": False,
        "target_changed": False,
        "close_changed": False,
        "reference_vol63_changed": False,
        "candidate_feature_change_scope": "asset_range_1d_pct on repaired assets only",
    }
    _write_immutable_json(report_path, report)
    gate = validate_ohlc_repair_gate(
        config_path, source_db, registry_db, report_root, origin_day
    )
    if gate["status"] != "PASS":
        raise RuntimeError("OHLC repair gate failed after materialization")
    return {**report, "gate": gate}


def acquire(
    config_path: Path,
    source_db: Path,
    registry_db: Path,
    raw_root: Path,
    report_root: Path,
    origin_day: str,
    limit: int | None,
    retry_failed: bool,
    progress_every: int,
    sleep_seconds: float | None,
):
    cfg = load_config(config_path)
    plan = refresh_plan(
        config_path, source_db, registry_db, origin_day
    )
    if plan["failures"]:
        raise RuntimeError("refresh plan failed: " + json.dumps(
            plan["failures"], sort_keys=True
        ))
    if plan["clock"]["status"] != "READY":
        raise RuntimeError("wait until the provider-settlement clock")
    path = report_root / origin_day / "acquisition_checkpoint.json"
    checkpoint = read_json(path) if path.is_file() else {
        "refresh_version": cfg["version"],
        "origin_day": origin_day,
        "fixed_fit_id": plan["fixed_fit_id"],
        "rows": {},
    }
    if (
        checkpoint.get("refresh_version") != cfg["version"]
        or checkpoint.get("origin_day") != origin_day
        or checkpoint.get("fixed_fit_id") != plan["fixed_fit_id"]
    ):
        raise RuntimeError("refresh checkpoint contract mismatch")
    delay = (
        float(cfg["sleep_seconds_between_provider_calls"])
        if sleep_seconds is None else float(sleep_seconds)
    )
    attempted = completed = failed = missing = 0
    for row in plan["assets"]:
        if row["status"] != "PENDING":
            continue
        ticker = row["ticker"]
        prior = checkpoint["rows"].get(ticker)
        if prior and prior.get("status") == "COMPLETED":
            continue
        if (
            prior
            and prior.get("status") in {"FAILED", "MISSING_ORIGIN"}
            and not retry_failed
        ):
            continue
        if limit is not None and attempted >= limit:
            break
        attempted += 1
        try:
            result = run_asset_refresh(
                db=source_db,
                raw_root=raw_root,
                ticker=ticker,
                provider_symbol=row["provider_symbol"],
                requested_start=row["requested_start"],
                requested_end=row["requested_end_exclusive"],
                exchange_override=row["exchange"],
                max_days=int(
                    cfg["maximum_incremental_request_calendar_days"]
                ),
                maximum_market_time_delay_seconds=int(
                    cfg["maximum_regular_market_time_delay_seconds"]
                ),
            )
            quality = quality_status(
                source_db, result["quality_run_id"]
            )
            with sqlite3.connect(source_db) as conn:
                present = bool(conn.execute(
                    """
                    SELECT 1
                    FROM daily_price_quality_gated_observations_v002
                    WHERE asset_id=? AND trading_day=? LIMIT 1
                    """,
                    (row["asset_id"], origin_day),
                ).fetchone())
            status = (
                "COMPLETED"
                if quality["status"] == "PASS" and present
                else "MISSING_ORIGIN"
            )
            checkpoint["rows"][ticker] = {
                "status": status,
                "asset_id": row["asset_id"],
                "ticker": ticker,
                "provider_symbol": row["provider_symbol"],
                "exchange": row["exchange"],
                "requested_start": row["requested_start"],
                "requested_end_exclusive": row[
                    "requested_end_exclusive"
                ],
                "run_id": result["run_id"],
                "retrieved_at_utc": result["retrieved_at_utc"],
                "quality_run_id": result["quality_run_id"],
                "origin_present": present,
                "quality": quality,
                "close_fallback": result.get("close_fallback"),
            }
            if status == "COMPLETED":
                completed += 1
            else:
                missing += 1
        except Exception as error:
            checkpoint["rows"][ticker] = {
                "status": "FAILED",
                "asset_id": row["asset_id"],
                "ticker": ticker,
                "error_type": type(error).__name__,
                "error": str(error),
            }
            failed += 1
        write_json(path, checkpoint)
        if progress_every > 0 and attempted % progress_every == 0:
            print(json.dumps({
                "progress": attempted,
                "completed": completed,
                "missing_origin": missing,
                "failed": failed,
            }, sort_keys=True), flush=True)
        if delay > 0:
            time.sleep(delay)
    counts: dict[str, int] = {}
    for row in checkpoint["rows"].values():
        status = str(row.get("status", "UNKNOWN"))
        counts[status] = counts.get(status, 0) + 1
    source = audit_source(config_path, source_db, origin_day)
    write_json(report_root / origin_day / "source_audit.json", source)
    return {
        "status": (
            "PASS_SOURCE_READY"
            if source["status"] == "PASS"
            else "INCOMPLETE_SOURCE_REFRESH"
        ),
        "origin_day": origin_day,
        "attempted": attempted,
        "completed": completed,
        "missing_origin": missing,
        "failed": failed,
        "checkpoint_counts": counts,
        "source_audit": source,
        "checkpoint_path": str(path),
    }


def audit_core_origin(
    core_db: Path,
    origin_day: str,
    feature_version: str,
):
    with sqlite3.connect(core_db) as conn:
        row = conn.execute(
            """
            SELECT COUNT(*),COUNT(DISTINCT asset_id),
                   COUNT(DISTINCT state_id),COUNT(DISTINCT state_time),
                   MIN(state_time),MAX(state_time)
            FROM market_daily_v003_states
            WHERE trading_day=? AND feature_version=?
            """,
            (origin_day, feature_version),
        ).fetchone()
        observed = conn.execute(
            """
            SELECT COUNT(*) FROM market_daily_v003_labels
            WHERE origin_trading_day=? AND horizon_sessions=1
              AND label_status <> 'insufficient_future'
            """,
            (origin_day,),
        ).fetchone()[0]
    return {
        "rows": int(row[0]),
        "assets": int(row[1]),
        "state_ids": int(row[2]),
        "state_times": int(row[3]),
        "minimum_state_time": row[4],
        "maximum_state_time": row[5],
        "observed_h1_labels": int(observed),
    }


def rebuild_core(
    config_path: Path,
    source_db: Path,
    core_db: Path,
    registry_db: Path,
    report_root: Path,
    origin_day: str,
):
    cfg = load_config(config_path)
    prereg, universe, v009 = frozen_contract(cfg)
    repair_gate = validate_ohlc_repair_gate(
        config_path, source_db, registry_db, report_root, origin_day
    )
    if repair_gate["status"] not in {"PASS", "NOT_REQUIRED"}:
        raise RuntimeError("OHLC operational amendment must PASS first")
    source = audit_source(config_path, source_db, origin_day)
    if source["status"] != "PASS":
        raise RuntimeError("source coverage must PASS first")
    fit = frozen_fit(registry_db, v009["version"])
    artifact = load_artifact(
        Path(fit["artifact_path"]),
        v009,
        prereg["feature_manifest_sha256"],
    )
    ids = [int(row["asset_id"]) for row in universe["assets"]]
    temporary = core_db.with_name(
        f"{core_db.name}.refreshing.{uuid.uuid4().hex}"
    )
    try:
        built = build_core_database(
            source_db, temporary, CORE_CONFIG
        )
        full = audit_core(source_db, temporary, CORE_CONFIG)
        if full["status"] != "PASS":
            raise RuntimeError(
                "rebuilt Core audit failed: "
                + json.dumps(full["failures"], sort_keys=True)
            )
        origin = audit_core_origin(
            temporary, origin_day, cfg["market_feature_version"]
        )
        if origin["assets"] < int(cfg["minimum_core_states_on_origin"]):
            raise RuntimeError("rebuilt Core origin coverage failed")
        if (
            origin["rows"] != origin["assets"]
            or origin["state_ids"] != origin["assets"]
            or origin["state_times"] != 1
        ):
            raise RuntimeError("rebuilt Core origin identity/clock failed")
        if origin["observed_h1_labels"] != 0:
            raise RuntimeError("origin H1 outcome is already observed")

        training = load_training_frame(temporary, v009, ids)
        training = training[
            training["target_trading_day"].astype(str)
            <= str(fit["training_last_target_day"])
        ].copy()
        columns = [
            "state_id", "asset_id", "origin_trading_day",
            "target_trading_day", "return_pct",
            *list(v009["frozen_own_features"]),
        ]
        training_hash = dataframe_sha256(training, columns)
        if (
            training_hash != fit["training_data_sha256"]
            or training_hash != artifact["training_data_sha256"]
        ):
            raise RuntimeError(
                "Core rebuild changed frozen V009 training data"
            )
        core_db.parent.mkdir(parents=True, exist_ok=True)
        os.replace(temporary, core_db)
    finally:
        temporary.unlink(missing_ok=True)
    report = {
        "status": "PASS_CORE_READY_FOR_V009_SEAL",
        "origin_day": origin_day,
        "fixed_fit_id": fit["fit_id"],
        "artifact_sha256": fit["artifact_sha256"],
        "frozen_training_data_sha256_preserved": training_hash,
        "source_audit": source,
        "ohlc_repair_gate": repair_gate,
        "core_origin_audit": origin,
        "core_build": built,
        "core_full_audit_status": full["status"],
        "refit_required": False,
    }
    write_json(
        report_root / origin_day / "core_refresh_audit.json",
        report,
    )
    return report


def readiness(
    config_path: Path,
    source_db: Path,
    core_db: Path,
    registry_db: Path,
    report_root: Path,
    origin_day: str,
):
    cfg = load_config(config_path)
    _, _, v009 = frozen_contract(cfg)
    fit = frozen_fit(registry_db, v009["version"])
    source = audit_source(config_path, source_db, origin_day)
    repair_gate = validate_ohlc_repair_gate(
        config_path, source_db, registry_db, report_root, origin_day
    )
    core = (
        audit_core_origin(
            core_db, origin_day, cfg["market_feature_version"]
        )
        if core_db.is_file() else None
    )
    core_ready = bool(
        core
        and core["assets"] >= int(cfg["minimum_core_states_on_origin"])
        and core["observed_h1_labels"] == 0
    )
    return {
        "status": (
            "READY_TO_SEAL_V009"
            if source["status"] == "PASS"
            and repair_gate["status"] in {"PASS", "NOT_REQUIRED"}
            and core_ready
            else "NOT_READY_TO_SEAL_V009"
        ),
        "origin_day": origin_day,
        "fixed_fit_id": fit["fit_id"],
        "source": source,
        "ohlc_repair_gate": repair_gate,
        "core": core,
        "refit_required": False,
    }


def cli_summary(payload: dict[str, Any]) -> dict[str, Any]:
    """Keep operational detail on disk without flooding the terminal."""
    summary = dict(payload)
    assets = summary.pop("assets", None)
    if assets is not None:
        counts: dict[str, int] = {}
        for row in assets:
            status = str(row.get("status", "UNKNOWN"))
            counts[status] = counts.get(status, 0) + 1
        summary["asset_status_counts"] = counts
    source = summary.get("source_audit")
    if isinstance(source, dict) and "observation_clock" in source:
        compact_source = dict(source)
        compact_source.pop("observation_clock", None)
        summary["source_audit"] = compact_source
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage", required=True,
        choices=(
            "plan", "acquire", "repair-ohlc", "build-core", "readiness"
        ),
    )
    parser.add_argument("--origin-day", required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--source-db", type=Path)
    parser.add_argument("--core-db", type=Path)
    parser.add_argument("--registry-db", type=Path)
    parser.add_argument("--raw-root", type=Path)
    parser.add_argument(
        "--report-root", type=Path, default=DEFAULT_REPORT_ROOT
    )
    parser.add_argument("--limit", type=int)
    parser.add_argument("--retry-failed", action="store_true")
    parser.add_argument("--progress-every", type=int, default=25)
    parser.add_argument("--sleep-seconds", type=float)
    args = parser.parse_args()
    cfg = load_config(args.config)
    source_db = args.source_db or root_path(cfg["source_database"])
    core_db = args.core_db or root_path(cfg["core_database"])
    registry_db = args.registry_db or root_path(
        cfg["registry_database"]
    )
    raw_root = args.raw_root or root_path(cfg["raw_root"])
    if args.stage == "plan":
        payload = refresh_plan(
            args.config, source_db, registry_db, args.origin_day
        )
        write_json(
            args.report_root / args.origin_day / "refresh_plan.json",
            payload,
        )
    elif args.stage == "acquire":
        payload = acquire(
            args.config, source_db, registry_db, raw_root,
            args.report_root, args.origin_day, args.limit,
            args.retry_failed, args.progress_every, args.sleep_seconds,
        )
    elif args.stage == "repair-ohlc":
        payload = repair_ohlc_origin(
            args.config, source_db, core_db, registry_db, raw_root,
            args.report_root, args.origin_day,
        )
    elif args.stage == "build-core":
        payload = rebuild_core(
            args.config, source_db, core_db, registry_db,
            args.report_root, args.origin_day,
        )
    else:
        payload = readiness(
            args.config, source_db, core_db, registry_db,
            args.report_root, args.origin_day,
        )
    print(json.dumps(
        cli_summary(payload), indent=2, sort_keys=True, allow_nan=False
    ))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import os
import sqlite3
import time
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from evaluation.market.daily_v003_core_audit import audit as audit_core
from features.market.daily_v003_core import (
    DEFAULT_CONFIG as CORE_CONFIG,
    build as build_core_database,
)
from ingestion.prices.yahoo_daily_broad_v003 import quality_status
from ingestion.prices.yahoo_daily_refresh_v009 import run_asset_refresh
from ingestion.prices.yahoo_daily_v1 import ExchangeCalendarResolver
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
    / "prospective_holdout_v001" / "daily_refresh"
)


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
    if cfg["version"] != "market_brain_daily_refresh_v009_v001":
        raise ValueError("unexpected refresh version")
    if cfg["supported_experiment_version"] != (
        "market_brain_distributional_v009_prospective_holdout_v001"
    ):
        raise ValueError("refresh is not bound to V009")
    if cfg["market_feature_version"] != "market_daily_state_v003_core":
        raise ValueError("refresh feature version changed")
    if cfg["label_version"] != "market_daily_reaction_v003_core":
        raise ValueError("refresh label version changed")
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
              FROM daily_price_quality_gated_observations_v001
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
            FROM daily_price_quality_gated_observations_v001
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
            )
            quality = quality_status(
                source_db, result["quality_run_id"]
            )
            with sqlite3.connect(source_db) as conn:
                present = bool(conn.execute(
                    """
                    SELECT 1
                    FROM daily_price_quality_gated_observations_v001
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
            }
            if status == "COMPLETED":
                completed += 1
            else:
                missing += 1
        except BaseException as error:
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
    origin_day: str,
):
    cfg = load_config(config_path)
    _, _, v009 = frozen_contract(cfg)
    fit = frozen_fit(registry_db, v009["version"])
    source = audit_source(config_path, source_db, origin_day)
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
            if source["status"] == "PASS" and core_ready
            else "NOT_READY_TO_SEAL_V009"
        ),
        "origin_day": origin_day,
        "fixed_fit_id": fit["fit_id"],
        "source": source,
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
        choices=("plan", "acquire", "build-core", "readiness"),
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
    elif args.stage == "build-core":
        payload = rebuild_core(
            args.config, source_db, core_db, registry_db,
            args.report_root, args.origin_day,
        )
    else:
        payload = readiness(
            args.config, source_db, core_db, registry_db,
            args.origin_day,
        )
    print(json.dumps(
        cli_summary(payload), indent=2, sort_keys=True, allow_nan=False
    ))


if __name__ == "__main__":
    main()

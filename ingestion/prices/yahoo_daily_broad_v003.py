from __future__ import annotations

import json
import sqlite3
import time
import uuid
from dataclasses import asdict, dataclass
from datetime import date
from pathlib import Path
from typing import Any, Callable

import pandas as pd

from ingestion.prices.yahoo_daily_v1 import (
    ABSOLUTE_MAX_DAYS,
    DEFAULT_DB,
    DEFAULT_RAW_ROOT,
    PROVIDER_TIMEOUT_SECONDS,
    RawPriceStore,
    SOURCE_ID,
    _configure_provider_exception_visibility,
    canonical_exchange,
    canonical_json as daily_canonical_json,
    ensure_contract,
    fetch_yahoo_daily,
    persist_provider_frame,
    resolve_asset,
    trading_day_from_index,
    utc_now,
    validate_pilot_window,
    validate_raw_root,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "market_brain_daily_v003_backfill.json"
DEFAULT_MANIFEST = (
    ROOT / "reports" / "market_brain_daily_v003" / "broad_backfill_manifest.json"
)
DEFAULT_CHECKPOINT = (
    ROOT / "reports" / "market_brain_daily_v003" / "broad_backfill_checkpoint.json"
)
QUALITY_VIEW = "daily_price_quality_gated_observations_v001"
DISCOVERY_VERSION = "market_daily_v003_yahoo_discovery_v001"
BACKFILL_VERSION = "market_daily_v003_yahoo_backfill_v001"


@dataclass(frozen=True)
class AssetCoverage:
    asset_id: int
    ticker: str
    sector: str | None
    exchange: str | None
    trading_days: int
    first_day: str | None
    last_day: str | None


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def stable_sha256(value: object) -> str:
    return hashlib_sha256(canonical_json(value).encode("utf-8"))


def hashlib_sha256(payload: bytes) -> str:
    import hashlib
    return hashlib.sha256(payload).hexdigest()


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    config = json.loads(path.read_text(encoding="utf-8"))
    start = date.fromisoformat(config["requested_start"])
    end = date.fromisoformat(config["requested_end_exclusive"])
    span = (end - start).days
    if span <= 0 or span > ABSOLUTE_MAX_DAYS:
        raise ValueError(
            f"Ventana broad inválida: {span} días; límite={ABSOLUTE_MAX_DAYS}"
        )
    if config.get("include_proxies") is not False:
        raise ValueError("V001 broad backfill must not include proxies")
    if config.get("include_macro") is not False:
        raise ValueError("V001 broad backfill must not include macro")
    return config


def atomic_write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_suffix(path.suffix + ".tmp")
    temporary.write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    temporary.replace(path)


def _objects(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = ?",
            (kind,),
        )
    }


def ensure_foundation_contract(conn: sqlite3.Connection) -> None:
    required_tables = {
        "assets",
        "price_quality_results",
        "price_quality_runs",
    }
    tables = _objects(conn, "table")
    views = _objects(conn, "view")
    missing_tables = sorted(required_tables - tables)
    missing_views = [] if QUALITY_VIEW in views else [QUALITY_VIEW]
    if missing_tables or missing_views:
        raise RuntimeError(
            "Market V003 foundation incompleta: "
            + json.dumps(
                {
                    "missing_tables": missing_tables,
                    "missing_views": missing_views,
                },
                sort_keys=True,
            )
        )


def coverage_rows(db: Path) -> list[AssetCoverage]:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        ensure_foundation_contract(conn)
        rows = conn.execute(
            f"""
            SELECT
                a.asset_id,
                a.ticker,
                a.sector,
                a.exchange,
                COUNT(DISTINCT q.trading_day) AS trading_days,
                MIN(q.trading_day) AS first_day,
                MAX(q.trading_day) AS last_day
            FROM assets a
            LEFT JOIN {QUALITY_VIEW} q
              ON q.asset_id = a.asset_id
            WHERE a.active = 1 AND a.asset_type = 'equity'
            GROUP BY
                a.asset_id, a.ticker, a.sector, a.exchange
            ORDER BY a.ticker
            """
        ).fetchall()
    return [
        AssetCoverage(
            asset_id=int(row["asset_id"]),
            ticker=str(row["ticker"]),
            sector=None if row["sector"] is None else str(row["sector"]),
            exchange=None if row["exchange"] is None else str(row["exchange"]),
            trading_days=int(row["trading_days"] or 0),
            first_day=None if row["first_day"] is None else str(row["first_day"]),
            last_day=None if row["last_day"] is None else str(row["last_day"]),
        )
        for row in rows
    ]


def preflight(db: Path, config: dict[str, Any]) -> dict[str, Any]:
    rows = coverage_rows(db)
    threshold = int(config["minimum_existing_days_to_skip"])
    ready = [row for row in rows if row.trading_days >= threshold]
    pending = [row for row in rows if row.trading_days < threshold]
    missing_exchange = [row.ticker for row in rows if not row.exchange]

    return {
        "status": "PASS",
        "version": BACKFILL_VERSION,
        "active_equities": len(rows),
        "existing_ready_assets": len(ready),
        "pending_assets": len(pending),
        "assets_missing_exchange_metadata": len(missing_exchange),
        "requested_start": config["requested_start"],
        "requested_end_exclusive": config["requested_end_exclusive"],
        "minimum_existing_days_to_skip": threshold,
        "ready_tickers": [row.ticker for row in ready],
        "pending_tickers": [row.ticker for row in pending],
        "scientific_contract": {
            "current_cohort_research": True,
            "survivorship_free": False,
            "historical_price_pit_verified": False,
            "dynamic_asset_entry": True,
            "proxies_included": False,
            "macro_included": False,
        },
    }


def _history_kwargs(
    requested_start: str,
    requested_end: str,
    provider_module: Any,
) -> dict[str, object]:
    kwargs: dict[str, object] = {
        "start": requested_start,
        "end": requested_end,
        "interval": "1d",
        "auto_adjust": False,
        "actions": True,
        "repair": False,
        "keepna": True,
        "timeout": PROVIDER_TIMEOUT_SECONDS,
    }
    if not _configure_provider_exception_visibility(provider_module):
        kwargs["raise_errors"] = True
    return kwargs


def _candidate_exchange_values(
    ticker_object: Any,
    history_metadata: dict[str, Any],
) -> list[str]:
    candidates: list[str] = []
    for key in (
        "exchangeName",
        "exchange",
        "fullExchangeName",
    ):
        value = history_metadata.get(key)
        if value:
            candidates.append(str(value))

    try:
        fast_info = ticker_object.fast_info
        for key in ("exchange", "exchange_name"):
            try:
                value = fast_info.get(key)
            except AttributeError:
                value = getattr(fast_info, key, None)
            if value:
                candidates.append(str(value))
    except BaseException:
        pass

    # Order-preserving de-duplication.
    return list(dict.fromkeys(candidates))


def resolve_exchange_from_candidates(
    candidates: list[str],
) -> tuple[str | None, list[str]]:
    rejected: list[str] = []
    for candidate in candidates:
        try:
            return canonical_exchange(candidate), rejected
        except (KeyError, ValueError):
            rejected.append(candidate)
    return None, rejected


def _first_trading_day(frame: Any) -> str:
    if frame is None or len(frame) == 0:
        raise ValueError("Yahoo Finance returned an empty discovery frame")
    first = frame.index[0]
    return trading_day_from_index(first)


def discover_one(
    *,
    ticker: str,
    provider_symbol: str,
    requested_start: str,
    requested_end: str,
    existing_exchange: str | None,
    exchange_override: str | None,
    ticker_factory: Callable[[str], Any] | None = None,
    provider_module: Any | None = None,
) -> dict[str, Any]:
    if provider_module is None:
        import yfinance as provider_module
    if ticker_factory is None:
        ticker_factory = provider_module.Ticker

    ticker_object = ticker_factory(provider_symbol)
    frame = ticker_object.history(
        **_history_kwargs(
            requested_start,
            requested_end,
            provider_module,
        )
    )
    first_day = _first_trading_day(frame)
    last_day = trading_day_from_index(frame.index[-1])

    metadata: dict[str, Any] = {}
    try:
        result = ticker_object.get_history_metadata()
        if isinstance(result, dict):
            metadata = dict(result)
    except BaseException:
        pass

    exchange: str | None = None
    exchange_source = None
    rejected: list[str] = []

    if exchange_override:
        exchange = canonical_exchange(exchange_override)
        exchange_source = "config_override"
    elif existing_exchange:
        exchange = canonical_exchange(existing_exchange)
        exchange_source = "assets.exchange"
    else:
        candidates = _candidate_exchange_values(ticker_object, metadata)
        exchange, rejected = resolve_exchange_from_candidates(candidates)
        if exchange is not None:
            exchange_source = "yfinance_metadata"

    if exchange is None:
        status = "REVIEW"
        failures = ["unresolved_exchange"]
    else:
        status = "READY"
        failures = []

    instrument_type = (
        metadata.get("instrumentType")
        or metadata.get("quoteType")
        or metadata.get("type")
    )

    return {
        "status": status,
        "failures": failures,
        "ticker": ticker,
        "provider_symbol": provider_symbol,
        "discovered_first_day": first_day,
        "discovered_last_day": last_day,
        "exchange": exchange,
        "exchange_source": exchange_source,
        "exchange_candidates_rejected": rejected,
        "instrument_type": instrument_type,
        "provider_library_version": str(
            getattr(provider_module, "__version__", "unknown")
        ),
        "discovery_request": {
            "start": requested_start,
            "end": requested_end,
            "interval": "1d",
            "auto_adjust": False,
            "actions": True,
        },
        "note": (
            "Discovery request is not persisted as a price observation. "
            "The exact effective [first_day,end) window is fetched again "
            "by yahoo_daily_v1 for causal/raw lineage."
        ),
    }


def _blank_manifest(config: dict[str, Any]) -> dict[str, Any]:
    return {
        "manifest_version": DISCOVERY_VERSION,
        "config_sha256": stable_sha256(config),
        "requested_start": config["requested_start"],
        "requested_end_exclusive": config["requested_end_exclusive"],
        "rows": {},
    }


def load_manifest(
    path: Path,
    config: dict[str, Any],
) -> dict[str, Any]:
    if not path.is_file():
        return _blank_manifest(config)
    manifest = json.loads(path.read_text(encoding="utf-8"))
    if manifest.get("manifest_version") != DISCOVERY_VERSION:
        raise RuntimeError("Manifest version mismatch")
    if manifest.get("config_sha256") != stable_sha256(config):
        raise RuntimeError(
            "Manifest was created with a different config. "
            "Archive/remove it deliberately before changing the experiment."
        )
    return manifest


def discover_pending(
    *,
    db: Path,
    config: dict[str, Any],
    manifest_path: Path,
    limit: int | None,
    retry_errors: bool,
    progress_every: int,
    sleep_seconds: float,
    ticker_factory: Callable[[str], Any] | None = None,
    provider_module: Any | None = None,
) -> dict[str, Any]:
    coverage = coverage_rows(db)
    threshold = int(config["minimum_existing_days_to_skip"])
    manifest = load_manifest(manifest_path, config)
    symbol_overrides = {
        str(k).upper(): str(v)
        for k, v in config.get("provider_symbol_overrides", {}).items()
    }
    exchange_overrides = {
        str(k).upper(): str(v)
        for k, v in config.get("exchange_overrides", {}).items()
    }

    candidates = [row for row in coverage if row.trading_days < threshold]
    attempted = 0
    ready = 0
    review = 0
    errors = 0

    for asset in candidates:
        prior = manifest["rows"].get(asset.ticker)
        if prior and prior.get("status") in {"READY", "SKIP_EXISTING"}:
            continue
        if (
            prior
            and prior.get("status") == "ERROR"
            and not retry_errors
        ):
            continue
        if limit is not None and attempted >= limit:
            break

        attempted += 1
        symbol = symbol_overrides.get(asset.ticker.upper(), asset.ticker)
        try:
            discovered = discover_one(
                ticker=asset.ticker,
                provider_symbol=symbol,
                requested_start=config["requested_start"],
                requested_end=config["requested_end_exclusive"],
                existing_exchange=asset.exchange,
                exchange_override=exchange_overrides.get(
                    asset.ticker.upper()
                ),
                ticker_factory=ticker_factory,
                provider_module=provider_module,
            )
            discovered.update(
                {
                    "asset_id": asset.asset_id,
                    "sector": asset.sector,
                    "existing_quality_gated_days": asset.trading_days,
                }
            )
            manifest["rows"][asset.ticker] = discovered
            if discovered["status"] == "READY":
                ready += 1
            else:
                review += 1
        except BaseException as error:
            manifest["rows"][asset.ticker] = {
                "status": "ERROR",
                "ticker": asset.ticker,
                "provider_symbol": symbol,
                "asset_id": asset.asset_id,
                "error_type": type(error).__name__,
                "error": str(error),
            }
            errors += 1

        atomic_write_json(manifest_path, manifest)
        if progress_every > 0 and attempted % progress_every == 0:
            print(
                json.dumps(
                    {
                        "progress": attempted,
                        "ready_this_run": ready,
                        "review_this_run": review,
                        "errors_this_run": errors,
                    },
                    sort_keys=True,
                )
            )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return manifest_summary(manifest, coverage, threshold)


def manifest_summary(
    manifest: dict[str, Any],
    coverage: list[AssetCoverage] | None = None,
    threshold: int | None = None,
) -> dict[str, Any]:
    rows = list(manifest.get("rows", {}).values())
    statuses: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", "UNKNOWN"))
        statuses[status] = statuses.get(status, 0) + 1

    exchanges: dict[str, int] = {}
    for row in rows:
        exchange = row.get("exchange")
        if exchange:
            exchanges[str(exchange)] = exchanges.get(str(exchange), 0) + 1

    result: dict[str, Any] = {
        "manifest_version": manifest.get("manifest_version"),
        "manifest_rows": len(rows),
        "status_counts": statuses,
        "exchange_counts": exchanges,
        "review_tickers": sorted(
            str(row["ticker"])
            for row in rows
            if row.get("status") == "REVIEW"
        ),
        "error_tickers": sorted(
            str(row["ticker"])
            for row in rows
            if row.get("status") == "ERROR"
        ),
    }
    if coverage is not None and threshold is not None:
        result["expected_discovery_assets"] = sum(
            row.trading_days < threshold for row in coverage
        )
        result["already_ready_assets"] = sum(
            row.trading_days >= threshold for row in coverage
        )
        result["discovery_complete"] = (
            len(rows)
            >= result["expected_discovery_assets"]
            and not result["review_tickers"]
            and not result["error_tickers"]
        )
    return result


def plan_audit(
    *,
    db: Path,
    config: dict[str, Any],
    manifest_path: Path,
) -> dict[str, Any]:
    coverage = coverage_rows(db)
    threshold = int(config["minimum_existing_days_to_skip"])
    manifest = load_manifest(manifest_path, config)
    summary = manifest_summary(manifest, coverage, threshold)

    failures = []
    if summary["manifest_rows"] < summary["expected_discovery_assets"]:
        failures.append("discovery_incomplete")
    if summary["review_tickers"]:
        failures.append("review_tickers_present")
    if summary["error_tickers"]:
        failures.append("discovery_errors_present")

    ready_rows = [
        row
        for row in manifest["rows"].values()
        if row.get("status") == "READY"
    ]
    malformed = []
    for row in ready_rows:
        if (
            not row.get("exchange")
            or not row.get("discovered_first_day")
            or not row.get("provider_symbol")
        ):
            malformed.append(row.get("ticker"))
    if malformed:
        failures.append("malformed_ready_rows")

    return {
        "status": "PASS" if not failures else "REVIEW",
        "failures": failures,
        **summary,
        "malformed_ready_tickers": sorted(
            str(x) for x in malformed if x is not None
        ),
        "requested_start": config["requested_start"],
        "requested_end_exclusive": config["requested_end_exclusive"],
        "scientific_note": (
            "Per-asset effective start is the first Yahoo daily row observed "
            "during discovery, preventing expected pre-listing sessions from "
            "being incorrectly treated as missing quality data."
        ),
    }


def _blank_checkpoint(
    config: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    return {
        "checkpoint_version": BACKFILL_VERSION,
        "config_sha256": stable_sha256(config),
        "manifest_sha256": stable_sha256(manifest),
        "rows": {},
    }


def load_checkpoint(
    path: Path,
    config: dict[str, Any],
    manifest: dict[str, Any],
) -> dict[str, Any]:
    if not path.is_file():
        return _blank_checkpoint(config, manifest)
    checkpoint = json.loads(path.read_text(encoding="utf-8"))
    if checkpoint.get("checkpoint_version") != BACKFILL_VERSION:
        raise RuntimeError("Checkpoint version mismatch")
    if checkpoint.get("config_sha256") != stable_sha256(config):
        raise RuntimeError("Checkpoint config hash mismatch")
    if checkpoint.get("manifest_sha256") != stable_sha256(manifest):
        raise RuntimeError(
            "Manifest changed after backfill started. "
            "Do not silently change the registered discovery plan."
        )
    return checkpoint



def run_asset_backfill(
    *,
    db: Path,
    raw_root: Path,
    ticker: str,
    provider_symbol: str,
    requested_start: str,
    requested_end: str,
    exchange_override: str,
) -> dict[str, Any]:
    """Persist one broad-v003 asset using the tested yahoo_daily_v1 layers.

    Unlike run_pilot(), this preserves a distinct source-ingestion mode and
    permits the provider symbol to differ from the internal asset ticker.
    """
    validate_pilot_window(
        requested_start,
        requested_end,
        ABSOLUTE_MAX_DAYS,
    )
    raw_root = validate_raw_root(raw_root)
    run_id = str(uuid.uuid4())
    started_at = utc_now()

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_contract(conn)
        asset_id, canonical_ticker, exchange = resolve_asset(
            conn,
            ticker,
            exchange_override,
        )
        checkpoint_before = daily_canonical_json(
            {
                "contract": BACKFILL_VERSION,
                "ticker": canonical_ticker,
                "provider_symbol": provider_symbol,
                "start": requested_start,
                "end": requested_end,
                "exchange": exchange,
                "interval": "1d",
                "research_universe": (
                    "current_asset_cohort_not_survivorship_free"
                ),
            }
        )
        conn.execute(
            """
            INSERT INTO source_ingestion_runs(
                run_id,
                source_id,
                mode,
                started_at,
                status,
                checkpoint_before_json
            )
            VALUES (?, ?, 'yahoo_daily_broad_v003', ?, 'running', ?)
            """,
            (run_id, SOURCE_ID, started_at, checkpoint_before),
        )
        conn.commit()

        try:
            frame, provider_version = fetch_yahoo_daily(
                provider_symbol,
                requested_start,
                requested_end,
            )
            result = persist_provider_frame(
                conn,
                RawPriceStore(raw_root),
                asset_id=asset_id,
                symbol=provider_symbol,
                exchange=exchange,
                requested_start=requested_start,
                requested_end=requested_end,
                retrieved_at=utc_now(),
                provider_library_version=provider_version,
                frame=frame,
                source_run_id=run_id,
            )
            finished_at = utc_now()
            conn.execute(
                """
                UPDATE source_ingestion_runs
                SET finished_at = ?,
                    status = 'completed',
                    checkpoint_after_json = ?,
                    documents_discovered = 1,
                    documents_inserted = ?,
                    documents_existing = ?
                WHERE run_id = ?
                """,
                (
                    finished_at,
                    daily_canonical_json(
                        {
                            "contract": BACKFILL_VERSION,
                            "raw_batch_id": result.raw_batch_id,
                            "batch_retrieval_id": (
                                result.batch_retrieval_id
                            ),
                            "bars_inserted": result.bars_inserted,
                            "bar_observations_inserted": (
                                result.bar_observations_inserted
                            ),
                            "actions_inserted": result.actions_inserted,
                            "action_observations_inserted": (
                                result.action_observations_inserted
                            ),
                        }
                    ),
                    1 if result.batch_inserted else 0,
                    0 if result.batch_inserted else 1,
                    run_id,
                ),
            )
            conn.commit()
            return {
                "run_id": run_id,
                "ticker": canonical_ticker,
                "provider_symbol": provider_symbol,
                "exchange": exchange,
                "provider_library_version": provider_version,
                **asdict(result),
            }
        except BaseException as error:
            conn.rollback()
            conn.execute(
                """
                UPDATE source_ingestion_runs
                SET finished_at = ?,
                    status = 'failed',
                    error_count = 1,
                    error_json = ?
                WHERE run_id = ?
                """,
                (
                    utc_now(),
                    daily_canonical_json(
                        {
                            "contract": BACKFILL_VERSION,
                            "error_type": type(error).__name__,
                            "message": str(error),
                        }
                    ),
                    run_id,
                ),
            )
            conn.commit()
            raise

def quality_status(
    db: Path,
    quality_run_id: str,
) -> dict[str, Any]:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = conn.execute(
            """
            SELECT check_name, check_status, observed_value, expected_value
            FROM price_quality_results
            WHERE quality_run_id = ?
            ORDER BY check_name
            """,
            (quality_run_id,),
        ).fetchall()
    failed = [
        dict(row) for row in rows if row["check_status"] == "fail"
    ]
    warnings = [
        dict(row) for row in rows if row["check_status"] == "warn"
    ]
    return {
        "status": "PASS" if not failed else "FAIL",
        "failed_checks": failed,
        "warning_checks": warnings,
        "checks": [dict(row) for row in rows],
    }


def checkpoint_summary(checkpoint: dict[str, Any]) -> dict[str, Any]:
    rows = list(checkpoint.get("rows", {}).values())
    counts: dict[str, int] = {}
    for row in rows:
        status = str(row.get("status", "UNKNOWN"))
        counts[status] = counts.get(status, 0) + 1
    return {
        "checkpoint_version": checkpoint.get("checkpoint_version"),
        "processed_assets": len(rows),
        "status_counts": counts,
        "completed_tickers": sorted(
            str(row["ticker"])
            for row in rows
            if row.get("status") == "COMPLETED"
        ),
        "failed_tickers": sorted(
            str(row["ticker"])
            for row in rows
            if row.get("status") == "FAILED"
        ),
    }


def run_backfill(
    *,
    db: Path,
    raw_root: Path,
    config: dict[str, Any],
    manifest_path: Path,
    checkpoint_path: Path,
    limit: int | None,
    retry_failed: bool,
    progress_every: int,
    sleep_seconds: float,
) -> dict[str, Any]:
    plan = plan_audit(
        db=db,
        config=config,
        manifest_path=manifest_path,
    )
    if plan["status"] != "PASS":
        raise RuntimeError(
            "Plan audit must PASS before backfill: "
            + json.dumps(plan, ensure_ascii=False)
        )

    manifest = load_manifest(manifest_path, config)
    checkpoint = load_checkpoint(checkpoint_path, config, manifest)
    ready_rows = sorted(
        (
            row
            for row in manifest["rows"].values()
            if row.get("status") == "READY"
        ),
        key=lambda row: str(row["ticker"]),
    )

    attempted = 0
    completed = 0
    failed = 0

    for row in ready_rows:
        ticker = str(row["ticker"])
        prior = checkpoint["rows"].get(ticker)
        if prior and prior.get("status") == "COMPLETED":
            continue
        if (
            prior
            and prior.get("status") == "FAILED"
            and not retry_failed
        ):
            continue
        if limit is not None and attempted >= limit:
            break

        attempted += 1
        try:
            result = run_asset_backfill(
                db=db,
                raw_root=raw_root,
                ticker=ticker,
                provider_symbol=str(row["provider_symbol"]),
                requested_start=str(row["discovered_first_day"]),
                requested_end=config["requested_end_exclusive"],
                exchange_override=str(row["exchange"]),
            )
            quality = quality_status(db, str(result["quality_run_id"]))
            status = (
                "COMPLETED" if quality["status"] == "PASS" else "FAILED"
            )
            checkpoint["rows"][ticker] = {
                "status": status,
                "ticker": ticker,
                "provider_symbol": row["provider_symbol"],
                "effective_start": row["discovered_first_day"],
                "requested_end_exclusive": config[
                    "requested_end_exclusive"
                ],
                "exchange": row["exchange"],
                "run_id": result["run_id"],
                "quality_run_id": result["quality_run_id"],
                "bars_discovered": result["bars_discovered"],
                "bars_inserted": result["bars_inserted"],
                "bar_observations_inserted": result[
                    "bar_observations_inserted"
                ],
                "actions_discovered": result["actions_discovered"],
                "quality": quality,
            }
            if status == "COMPLETED":
                completed += 1
            else:
                failed += 1
        except BaseException as error:
            checkpoint["rows"][ticker] = {
                "status": "FAILED",
                "ticker": ticker,
                "provider_symbol": row.get("provider_symbol"),
                "effective_start": row.get("discovered_first_day"),
                "exchange": row.get("exchange"),
                "error_type": type(error).__name__,
                "error": str(error),
            }
            failed += 1

        atomic_write_json(checkpoint_path, checkpoint)
        if progress_every > 0 and attempted % progress_every == 0:
            print(
                json.dumps(
                    {
                        "progress": attempted,
                        "completed_this_run": completed,
                        "failed_this_run": failed,
                    },
                    sort_keys=True,
                )
            )
        if sleep_seconds > 0:
            time.sleep(sleep_seconds)

    return checkpoint_summary(checkpoint)

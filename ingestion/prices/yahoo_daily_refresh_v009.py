from __future__ import annotations

import sqlite3
import uuid
from dataclasses import asdict
from pathlib import Path
from typing import Any

from ingestion.prices.yahoo_daily_v1 import (
    SOURCE_ID,
    RawPriceStore,
    canonical_json,
    ensure_contract,
    fetch_yahoo_daily,
    persist_provider_frame,
    resolve_asset,
    utc_now,
    validate_pilot_window,
    validate_raw_root,
)

REFRESH_MODE = "yahoo_daily_refresh_v009_v001"


def run_asset_refresh(
    *,
    db: Path,
    raw_root: Path,
    ticker: str,
    provider_symbol: str,
    requested_start: str,
    requested_end: str,
    exchange_override: str,
    max_days: int,
) -> dict[str, Any]:
    """Retrieve one short live window with append-only provider lineage."""
    validate_pilot_window(requested_start, requested_end, max_days)
    raw_root = validate_raw_root(raw_root)
    run_id = str(uuid.uuid4())
    started_at = utc_now()

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        ensure_contract(conn)
        asset_id, canonical_ticker, exchange = resolve_asset(
            conn,
            ticker,
            exchange_override,
        )
        checkpoint_before = canonical_json(
            {
                "contract": REFRESH_MODE,
                "ticker": canonical_ticker,
                "provider_symbol": provider_symbol,
                "start": requested_start,
                "end": requested_end,
                "exchange": exchange,
                "interval": "1d",
                "purpose": "prospective_v009_state_materialization",
            }
        )
        conn.execute(
            """
            INSERT INTO source_ingestion_runs(
              run_id,source_id,mode,started_at,status,checkpoint_before_json
            ) VALUES (?,?,?,?,'running',?)
            """,
            (
                run_id,
                SOURCE_ID,
                REFRESH_MODE,
                started_at,
                checkpoint_before,
            ),
        )
        conn.commit()

        try:
            frame, provider_version = fetch_yahoo_daily(
                provider_symbol,
                requested_start,
                requested_end,
            )
            retrieved_at = utc_now()
            result = persist_provider_frame(
                conn,
                RawPriceStore(raw_root),
                asset_id=asset_id,
                symbol=provider_symbol,
                exchange=exchange,
                requested_start=requested_start,
                requested_end=requested_end,
                retrieved_at=retrieved_at,
                provider_library_version=provider_version,
                frame=frame,
                source_run_id=run_id,
            )
            finished_at = utc_now()
            result_payload = {
                "contract": REFRESH_MODE,
                "raw_batch_id": result.raw_batch_id,
                "batch_retrieval_id": result.batch_retrieval_id,
                "quality_run_id": result.quality_run_id,
                "bars_discovered": result.bars_discovered,
                "bars_inserted": result.bars_inserted,
                "bar_observations_inserted": (
                    result.bar_observations_inserted
                ),
            }
            conn.execute(
                """
                UPDATE source_ingestion_runs
                SET finished_at=?,status='completed',
                    checkpoint_after_json=?,documents_discovered=1,
                    documents_inserted=?,documents_existing=?
                WHERE run_id=?
                """,
                (
                    finished_at,
                    canonical_json(result_payload),
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
                "retrieved_at_utc": retrieved_at,
                "provider_library_version": provider_version,
                **asdict(result),
            }
        except BaseException as error:
            conn.rollback()
            conn.execute(
                """
                UPDATE source_ingestion_runs
                SET finished_at=?,status='failed',error_count=1,error_json=?
                WHERE run_id=?
                """,
                (
                    utc_now(),
                    canonical_json(
                        {
                            "contract": REFRESH_MODE,
                            "error_type": type(error).__name__,
                            "message": str(error),
                        }
                    ),
                    run_id,
                ),
            )
            conn.commit()
            raise

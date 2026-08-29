from __future__ import annotations

import math
import sqlite3
import uuid
from dataclasses import asdict
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any

from ingestion.prices.yahoo_daily_v1 import (
    SOURCE_ID,
    ExchangeCalendarResolver,
    RawPriceStore,
    canonical_json,
    ensure_contract,
    fetch_yahoo_daily,
    number_or_none,
    persist_provider_frame,
    resolve_asset,
    trading_day_from_index,
    utc_now,
    validate_pilot_window,
    validate_raw_root,
)

REFRESH_MODE = "yahoo_daily_refresh_v009_v002"
REGULAR_CLOSE_FALLBACK = "regular_market_price_fallback_v001"


def _metadata_time_utc(value: Any) -> datetime:
    if isinstance(value, (int, float)) and math.isfinite(float(value)):
        return datetime.fromtimestamp(float(value), tz=timezone.utc)
    if hasattr(value, "to_pydatetime"):
        value = value.to_pydatetime()
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError("regularMarketTime has no timezone")
        return value.astimezone(timezone.utc)
    raise ValueError("regularMarketTime is missing or invalid")


def apply_regular_close_fallback(
    frame: Any,
    metadata: dict[str, Any],
    *,
    origin_day: str,
    exchange: str,
    retrieved_at: str,
    maximum_market_time_delay_seconds: int,
) -> tuple[Any, dict[str, Any]]:
    """Fill only a missing daily Close from same-session regular metadata."""
    matches = [
        index
        for index in frame.index
        if trading_day_from_index(index) == origin_day
    ]
    if len(matches) != 1:
        raise ValueError(
            f"expected one provider row for origin {origin_day}; "
            f"found {len(matches)}"
        )
    index = matches[0]
    result = frame.copy()
    result["Provider Daily Close"] = result["Close"]
    result["Close Source"] = "daily_history_close"
    result["Regular Market Price"] = float("nan")
    result["Regular Market Time UTC"] = None

    existing_close = number_or_none(result.at[index, "Close"])
    if existing_close is not None:
        return result, {
            "policy": REGULAR_CLOSE_FALLBACK,
            "applied": False,
            "origin_day": origin_day,
            "close_source": "daily_history_close",
        }

    price = number_or_none(metadata.get("regularMarketPrice"))
    if price is None or float(price) <= 0:
        raise ValueError("regularMarketPrice is missing or nonpositive")
    market_time = _metadata_time_utc(metadata.get("regularMarketTime"))
    retrieved = datetime.fromisoformat(retrieved_at.replace("Z", "+00:00"))
    if retrieved.tzinfo is None:
        raise ValueError("retrieved_at has no timezone")
    retrieved = retrieved.astimezone(timezone.utc)

    session = ExchangeCalendarResolver(
        exchange, start=origin_day, end=origin_day
    ).bounds(origin_day)
    if session is None:
        raise ValueError(f"origin {origin_day} is not an exchange session")
    delay = (market_time - session.close_utc).total_seconds()
    if delay < 0 or delay > int(maximum_market_time_delay_seconds):
        raise ValueError(
            "regularMarketTime is outside the allowed close boundary: "
            f"delay_seconds={delay}"
        )
    if market_time > retrieved:
        raise ValueError("regularMarketTime is later than retrieval")

    opened = number_or_none(result.at[index, "Open"])
    high = number_or_none(result.at[index, "High"])
    low = number_or_none(result.at[index, "Low"])
    if opened is None or high is None or low is None:
        raise ValueError("origin Open/High/Low is incomplete")
    if not (float(low) <= float(price) <= float(high)):
        raise ValueError("regularMarketPrice is outside daily Low/High")

    result.at[index, "Close"] = float(price)
    result.at[index, "Close Source"] = REGULAR_CLOSE_FALLBACK
    result.at[index, "Regular Market Price"] = float(price)
    result.at[index, "Regular Market Time UTC"] = market_time.isoformat()
    return result, {
        "policy": REGULAR_CLOSE_FALLBACK,
        "applied": True,
        "origin_day": origin_day,
        "close_source": "regularMarketPrice",
        "regular_market_price": float(price),
        "regular_market_time_utc": market_time.isoformat(),
        "session_close_utc": session.close_utc.isoformat(),
        "market_time_delay_seconds": delay,
        "adjusted_close_filled": False,
        "post_market_price_used": False,
    }


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
    maximum_market_time_delay_seconds: int = 300,
) -> dict[str, Any]:
    """Retrieve one short live window with append-only provider lineage."""
    validate_pilot_window(requested_start, requested_end, max_days)
    raw_root = validate_raw_root(raw_root)
    run_id = str(uuid.uuid4())
    started_at = utc_now()
    origin_day = (
        date.fromisoformat(requested_end) - timedelta(days=1)
    ).isoformat()

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
                "origin_day": origin_day,
                "exchange": exchange,
                "interval": "1d",
                "close_fallback": REGULAR_CLOSE_FALLBACK,
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
            import yfinance as provider_module

            ticker_object = provider_module.Ticker(provider_symbol)
            frame, provider_version = fetch_yahoo_daily(
                provider_symbol,
                requested_start,
                requested_end,
                ticker_factory=lambda unused: ticker_object,
                provider_module=provider_module,
            )
            metadata = ticker_object.get_history_metadata()
            retrieved_at = utc_now()
            frame, fallback = apply_regular_close_fallback(
                frame,
                dict(metadata or {}),
                origin_day=origin_day,
                exchange=exchange,
                retrieved_at=retrieved_at,
                maximum_market_time_delay_seconds=(
                    maximum_market_time_delay_seconds
                ),
            )
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
                "close_fallback": fallback,
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
                "close_fallback": fallback,
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
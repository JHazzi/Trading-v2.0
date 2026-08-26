from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import math
import os
import sqlite3
import uuid
from collections import Counter
from dataclasses import asdict, dataclass
from datetime import date, datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Callable, Iterable

import pandas as pd


SOURCE_ID = "yahoo_finance"
BATCH_VERSION = "yahoo_daily_batch_v2"
PARSER_VERSION = "yahoo_daily_parser_v2"
QUALITY_VERSION = "daily_price_quality_v2"
ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
DEFAULT_RAW_ROOT = ROOT / "data" / "raw"
DEFAULT_MAX_DAYS = 366
ABSOLUTE_MAX_DAYS = 3660
PROVIDER_TIMEOUT_SECONDS = 30
EXCEPTION_VISIBILITY_CONTRACT = (
    "yfinance_config_debug_hide_exceptions_false_or_legacy_raise_errors"
)
CORE_PRICE_COLUMNS = {"Open", "High", "Low", "Close", "Volume"}
ACTION_COLUMNS = (
    ("Dividends", "dividend"),
    ("Stock Splits", "stock_split"),
    ("Capital Gains", "capital_gain"),
)

# Explicit aliases only. Persisted exchange identities are canonical.
EXCHANGE_CANONICAL_MAP = {
    "XNYS": "XNYS",
    "NYSE": "XNYS",
    "NYQ": "XNYS",
    "XNAS": "XNAS",
    "NASDAQ": "XNAS",
    "NMS": "XNAS",
    "NGM": "XNAS",
    "NCM": "XNAS",
    "BATS": "BATS",
    "BZX": "BATS",
    "BTS": "BATS",
    "CBOE BZX": "BATS",
    "BZX EQUITIES": "BATS",
    'ARCX': 'ARCX',
    'ARCA': 'ARCX',
    'NYSE ARCA': 'ARCX',
    'NYSEARCA': 'ARCX',
    'PCX': 'ARCX',
}
EXCHANGE_CALENDAR_MAP = {
    "XNYS": "XNYS",
    "XNAS": "XNAS",
    "BATS": "BATS",
    'ARCX': 'XNYS',
}

REQUIRED_TABLES = {
    "assets",
    "ingestion_sources",
    "source_ingestion_runs",
    "raw_price_batches",
    "raw_price_batch_retrievals",
    "price_bar_versions",
    "price_bar_observations",
    "corporate_action_versions",
    "corporate_action_observations",
    "asset_identifier_history",
    "price_quality_runs",
    "price_quality_results",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def parse_utc(value: str | datetime) -> datetime:
    if isinstance(value, datetime):
        parsed = value
    else:
        parsed = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        raise ValueError(f"Timestamp sin zona horaria: {value!r}")
    return parsed.astimezone(timezone.utc)


def stable_id(prefix: str, *parts: object) -> str:
    payload = "\0".join(str(part) for part in parts).encode("utf-8")
    return f"{prefix}_{hashlib.sha256(payload).hexdigest()}"


def canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        allow_nan=False,
        sort_keys=True,
        separators=(",", ":"),
    )


def canonical_scalar(value: object) -> object:
    if value is None:
        return None

    try:
        missing = pd.isna(value)
        if not hasattr(missing, "__len__") and bool(missing):
            return None
    except (TypeError, ValueError):
        pass

    item = getattr(value, "item", None)
    if callable(item):
        try:
            value = item()
        except (TypeError, ValueError):
            pass

    if isinstance(value, (datetime, date)):
        return value.isoformat()
    if isinstance(value, bool):
        return value
    if isinstance(value, int):
        return value
    if isinstance(value, float):
        return None if not math.isfinite(value) else value
    if isinstance(value, str):
        return value

    return str(value)


def number_or_none(value: object) -> float | None:
    normalized = canonical_scalar(value)
    if normalized is None or isinstance(normalized, bool):
        return None
    try:
        result = float(normalized)
    except (TypeError, ValueError):
        return None
    return result if math.isfinite(result) else None


def trading_day_from_index(value: object) -> str:
    if isinstance(value, datetime):
        if value.tzinfo is None:
            raise ValueError(
                f"Índice datetime sin zona horaria: {value!r}"
            )
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    text = str(value)
    if len(text) == 10:
        return date.fromisoformat(text).isoformat()
    try:
        parsed = datetime.fromisoformat(text.replace("Z", "+00:00"))
        if parsed.tzinfo is None:
            raise ValueError(
                f"Índice datetime sin zona horaria: {value!r}"
            )
        return parsed.date().isoformat()
    except ValueError as error:
        raise ValueError(
            f"Índice diario inválido o sin zona horaria: {value!r}"
        ) from error


def _frame_column_projection(
    frame: Any,
    symbol: str,
) -> tuple[list[str], dict[str, object]]:
    provider_columns = list(frame.columns)
    if not provider_columns:
        raise ValueError("El frame del proveedor no contiene columnas")

    tuple_flags = [isinstance(column, tuple) for column in provider_columns]
    if any(tuple_flags) and not all(tuple_flags):
        raise ValueError(
            "El frame mezcla columnas simples y MultiIndex"
        )

    if all(tuple_flags):
        widths = {len(column) for column in provider_columns}
        if len(widths) != 1:
            raise ValueError("MultiIndex con tuplas de distinto tamaño")
        width = widths.pop()
        levels = [
            [str(column[level]) for column in provider_columns]
            for level in range(width)
        ]
        field_levels = [
            level
            for level, values in enumerate(levels)
            if CORE_PRICE_COLUMNS.issubset(set(values))
        ]
        if len(field_levels) != 1:
            raise ValueError(
                "No se pudo identificar un único nivel de campos OHLCV"
            )
        field_level = field_levels[0]
        other_levels = [level for level in range(width) if level != field_level]
        if any(len(set(levels[level])) != 1 for level in other_levels):
            raise ValueError(
                "Se recibió un frame multi-ticker; el piloto admite uno"
            )
        constants = {
            value.upper()
            for level in other_levels
            for value in set(levels[level])
        }
        if other_levels and symbol.upper() not in constants:
            raise ValueError(
                "El ticker del MultiIndex no coincide con el solicitado"
            )
        normalized = levels[field_level]
        encoded_columns: list[object] = [
            [canonical_scalar(part) for part in column]
            for column in provider_columns
        ]
    else:
        normalized = [str(column) for column in provider_columns]
        encoded_columns = [canonical_scalar(column) for column in provider_columns]

    if len(normalized) != len(set(normalized)):
        raise ValueError("El frame del proveedor contiene columnas duplicadas")
    missing = sorted(CORE_PRICE_COLUMNS - set(normalized))
    if missing:
        raise ValueError(f"Faltan columnas OHLCV requeridas: {missing}")

    schema = {
        "provider_columns": encoded_columns,
        "provider_column_names": [
            canonical_scalar(name)
            for name in list(getattr(frame.columns, "names", []))
        ],
        "normalized_columns": normalized,
    }
    return normalized, schema


def canonical_frame_rows(
    frame: Any,
    symbol: str,
) -> tuple[dict[str, object], list[dict[str, object]]]:
    columns, schema = _frame_column_projection(frame, symbol)

    rows: list[dict[str, object]] = []
    for row_number, (index_value, series) in enumerate(frame.iterrows()):
        values = {
            column: canonical_scalar(series.iloc[position])
            for position, column in enumerate(columns)
        }
        rows.append(
            {
                "provider_row_number": row_number,
                "index": canonical_scalar(index_value),
                "trading_day": trading_day_from_index(index_value),
                "values": values,
            }
        )
    return schema, rows


def canonical_provider_payload(
    frame: Any,
    *,
    symbol: str,
    requested_start: str,
    requested_end: str,
    provider_library_version: str,
    exchange: str,
    calendar_name: str,
) -> tuple[bytes, list[dict[str, object]]]:
    column_schema, rows = canonical_frame_rows(frame, symbol)
    payload = {
        "lineage_kind": "provider_library_output",
        "is_exact_http_response": False,
        "provider_library": {
            "name": "yfinance",
            "version": provider_library_version,
        },
        "request": {
            "symbol": symbol.upper(),
            "start": requested_start,
            "end": requested_end,
            "interval": "1d",
            "auto_adjust": False,
            "actions": True,
            "repair": False,
            "keepna": True,
            "timeout_seconds": PROVIDER_TIMEOUT_SECONDS,
            "exceptions_visible": True,
            "exception_visibility_contract": (
                EXCEPTION_VISIBILITY_CONTRACT
            ),
        },
        "normalization_context": {
            "exchange": exchange,
            "calendar_name": calendar_name,
        },
        "frame": {
            "column_schema": column_schema,
            "index_name": canonical_scalar(getattr(frame.index, "name", None)),
            "rows": rows,
        },
    }
    return canonical_json(payload).encode("utf-8"), rows


@dataclass(frozen=True)
class SessionBounds:
    exchange: str
    calendar_name: str
    trading_day: str
    open_utc: datetime
    close_utc: datetime


def canonical_exchange(exchange: str) -> str:
    normalized = exchange.strip().upper()
    try:
        return EXCHANGE_CANONICAL_MAP[normalized]
    except KeyError as error:
        accepted = ", ".join(sorted(EXCHANGE_CANONICAL_MAP))
        raise ValueError(
            f"Exchange no mapeado: {exchange!r}. Aceptados: {accepted}"
        ) from error


def calendar_name_for_exchange(exchange: str) -> str:
    return EXCHANGE_CALENDAR_MAP[canonical_exchange(exchange)]


class ExchangeCalendarResolver:
    def __init__(
        self,
        exchange: str,
        *,
        start: str | date | None = None,
        end: str | date | None = None,
    ) -> None:
        import exchange_calendars

        self.exchange = canonical_exchange(exchange)
        self.calendar_name = calendar_name_for_exchange(self.exchange)

        def padded_boundary(
            value: str | date | None,
            days: int,
        ) -> date | None:
            if value is None:
                return None
            parsed = (
                value
                if isinstance(value, date)
                else date.fromisoformat(value)
            )
            return parsed + timedelta(days=days)

        self.calendar = exchange_calendars.get_calendar(
            self.calendar_name,
            start=padded_boundary(start, -31),
            end=padded_boundary(end, 31),
        )

    def bounds(self, trading_day: str) -> SessionBounds | None:
        if not self.calendar.is_session(trading_day):
            return None
        opened = self.calendar.session_open(trading_day).to_pydatetime()
        closed = self.calendar.session_close(trading_day).to_pydatetime()
        return SessionBounds(
            exchange=self.exchange,
            calendar_name=self.calendar_name,
            trading_day=trading_day,
            open_utc=parse_utc(opened),
            close_utc=parse_utc(closed),
        )


class RawPriceStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _verify(
        path: Path,
        expected_payload: bytes,
        expected_digest: str,
    ) -> None:
        try:
            with gzip.open(path, "rb") as stream:
                stored = stream.read()
        except (OSError, EOFError) as error:
            raise RuntimeError(
                f"Raw de precios corrupto o ilegible: {path}"
            ) from error
        actual_digest = hashlib.sha256(stored).hexdigest()
        if actual_digest != expected_digest or stored != expected_payload:
            raise RuntimeError(
                "Colisión o corrupción en el raw content-addressed: "
                f"{path}"
            )

    def write(self, symbol: str, payload: bytes) -> tuple[Path, str, int]:
        digest = hashlib.sha256(payload).hexdigest()
        safe_symbol = "".join(
            character
            if character.isalnum() or character in "._-"
            else "_"
            for character in symbol.upper()
        )
        destination = (
            self.root
            / "prices"
            / SOURCE_ID
            / "daily"
            / safe_symbol
            / digest[:2]
            / f"{digest}.provider.json.gz"
        )
        destination.parent.mkdir(parents=True, exist_ok=True)
        if destination.exists():
            self._verify(destination, payload, digest)
            return destination, digest, len(payload)

        temporary = destination.with_suffix(
            destination.suffix + f".{uuid.uuid4().hex}.tmp"
        )
        try:
            with temporary.open("wb") as raw_stream:
                with gzip.GzipFile(
                    filename="",
                    mode="wb",
                    fileobj=raw_stream,
                    mtime=0,
                ) as stream:
                    stream.write(payload)
            try:
                os.link(temporary, destination)
            except FileExistsError:
                pass
        finally:
            temporary.unlink(missing_ok=True)
        self._verify(destination, payload, digest)
        return destination, digest, len(payload)


@dataclass(frozen=True)
class PersistResult:
    raw_batch_id: str
    raw_sha256: str
    batch_inserted: bool
    batch_retrieval_id: str
    batch_retrieval_inserted: bool
    bars_discovered: int
    bars_inserted: int
    bars_existing: int
    bar_observations_inserted: int
    bar_observations_existing: int
    bars_without_session: int
    bars_incomplete_session: int
    bars_duplicate_trading_day: int
    actions_discovered: int
    actions_inserted: int
    actions_existing: int
    action_observations_inserted: int
    action_observations_existing: int
    quality_run_id: str


def ensure_contract(conn: sqlite3.Connection) -> None:
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = sorted(REQUIRED_TABLES - existing)
    if missing:
        raise RuntimeError(
            f"Falta aplicar la migración 013; tablas ausentes: {missing}"
        )
    source = conn.execute(
        "SELECT 1 FROM ingestion_sources WHERE source_id = ?",
        (SOURCE_ID,),
    ).fetchone()
    if source is None:
        raise RuntimeError("La fuente yahoo_finance no está registrada")


def _normalized_price_row(
    raw_row: dict[str, object],
    session_resolver: Any,
    retrieved_at: datetime,
) -> dict[str, object]:
    trading_day = str(raw_row["trading_day"])
    session = session_resolver.bounds(trading_day)
    values = dict(raw_row["values"])
    normalized: dict[str, object] = {
        "provider_row_number": int(raw_row["provider_row_number"]),
        "trading_day": trading_day,
        "values": values,
        "session": session,
        "open": number_or_none(values.get("Open")),
        "high": number_or_none(values.get("High")),
        "low": number_or_none(values.get("Low")),
        "close": number_or_none(values.get("Close")),
        "volume": number_or_none(values.get("Volume")),
        "adjusted_close": number_or_none(values.get("Adj Close")),
    }
    normalized["is_complete"] = (
        session is not None and session.close_utc <= retrieved_at
    )
    normalized["initial_available_at"] = (
        session.close_utc if session is not None else None
    )
    return normalized


def _action_observations(
    row: dict[str, object],
) -> Iterable[tuple[str, float | None]]:
    values = dict(row["values"])
    for column, action_type in ACTION_COLUMNS:
        if column not in values:
            continue
        value = number_or_none(values[column])
        if value is None:
            continue
        yield action_type, None if value == 0.0 else value


def _quality_checks(
    rows: list[dict[str, object]],
    *,
    requested_start: str,
    requested_end: str,
    session_resolver: Any,
    retrieved: datetime,
) -> list[dict[str, object]]:
    days = [str(row["trading_day"]) for row in rows]
    day_counts = Counter(days)
    duplicate_days = sorted(
        day for day, count in day_counts.items() if count > 1
    )
    duplicate_count = sum(day_counts[day] - 1 for day in duplicate_days)
    counts = {
        "non_session_rows": 0,
        "incomplete_session_rows": 0,
        "missing_ohlc_rows": 0,
        "invalid_ohlc_rows": 0,
        "negative_volume_rows": 0,
        "missing_adjusted_close_rows": 0,
        "availability_before_close_rows": 0,
    }

    for row in rows:
        session = row["session"]
        if session is None:
            counts["non_session_rows"] += 1
            continue
        if not row["is_complete"]:
            counts["incomplete_session_rows"] += 1
            continue
        ohlc = [row["open"], row["high"], row["low"], row["close"]]
        if any(value is None for value in ohlc):
            counts["missing_ohlc_rows"] += 1
        else:
            opened, high, low, closed = (float(value) for value in ohlc)
            if high < max(opened, low, closed) or low > min(
                opened, high, closed
            ):
                counts["invalid_ohlc_rows"] += 1
        volume = row["volume"]
        if volume is not None and float(volume) < 0:
            counts["negative_volume_rows"] += 1
        if row["adjusted_close"] is None:
            counts["missing_adjusted_close_rows"] += 1
        available = row["initial_available_at"]
        if available is not None and available < session.close_utc:
            counts["availability_before_close_rows"] += 1

    start = date.fromisoformat(requested_start)
    end = date.fromisoformat(requested_end)
    expected_closed_sessions: set[str] = set()
    current = start
    while current < end:
        trading_day = current.isoformat()
        session = session_resolver.bounds(trading_day)
        if session is not None and session.close_utc <= retrieved:
            expected_closed_sessions.add(trading_day)
        current += timedelta(days=1)

    accepted_received_sessions = {
        str(row["trading_day"])
        for row in rows
        if day_counts[str(row["trading_day"])] == 1
        and row["session"] is not None
        and bool(row["is_complete"])
    }
    missing_sessions = sorted(
        expected_closed_sessions - accepted_received_sessions
    )

    raw_checks: list[tuple[str, int, int, str, dict[str, object]]] = [
        (
            "non_empty_batch",
            len(rows),
            1,
            "pass" if rows else "fail",
            {},
        ),
        (
            "duplicate_trading_days",
            duplicate_count,
            0,
            "pass" if duplicate_count == 0 else "fail",
            {"duplicate_days": duplicate_days},
        ),
        (
            "missing_expected_sessions",
            len(missing_sessions),
            0,
            "pass" if not missing_sessions else "fail",
            {
                "missing_trading_days": missing_sessions,
                "expected_closed_session_count": len(
                    expected_closed_sessions
                ),
                "accepted_received_session_count": len(
                    accepted_received_sessions
                ),
            },
        ),
    ]
    for name, observed in counts.items():
        failure_status = (
            "warn" if name == "missing_adjusted_close_rows" else "fail"
        )
        raw_checks.append(
            (
                name,
                observed,
                0,
                "pass" if observed == 0 else failure_status,
                {},
            )
        )
    return [
        {
            "check_name": name,
            "observed_value": float(observed),
            "expected_value": float(expected),
            "check_status": status,
            "details": details,
        }
        for name, observed, expected, status, details in raw_checks
    ]


def _persist_quality(
    conn: sqlite3.Connection,
    *,
    raw_batch_id: str,
    batch_retrieval_id: str,
    asset_id: int,
    rows: list[dict[str, object]],
    requested_start: str,
    requested_end: str,
    session_resolver: Any,
    retrieved: datetime,
    checked_at: str,
) -> str:
    quality_run_id = stable_id(
        "pqr", batch_retrieval_id, QUALITY_VERSION
    )
    configuration = canonical_json(
        {
            "quality_version": QUALITY_VERSION,
            "primary_price_field": "unadjusted_ohlcv",
            "adjusted_close_role": "audit_only_not_version_identity",
            "requested_start": requested_start,
            "requested_end": requested_end,
        }
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO price_quality_runs(
            quality_run_id,
            raw_batch_id,
            batch_retrieval_id,
            source_id,
            quality_version,
            started_at,
            finished_at,
            status,
            configuration_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, 'completed', ?)
        """,
        (
            quality_run_id,
            raw_batch_id,
            batch_retrieval_id,
            SOURCE_ID,
            QUALITY_VERSION,
            checked_at,
            checked_at,
            configuration,
        ),
    )
    checks = _quality_checks(
        rows,
        requested_start=requested_start,
        requested_end=requested_end,
        session_resolver=session_resolver,
        retrieved=retrieved,
    )
    for check in checks:
        result_id = stable_id(
            "pqc", quality_run_id, check["check_name"]
        )
        details = {
            "quality_version": QUALITY_VERSION,
            **dict(check["details"]),
        }
        conn.execute(
            """
            INSERT OR IGNORE INTO price_quality_results(
                quality_result_id,
                quality_run_id,
                asset_id,
                raw_batch_id,
                check_name,
                check_status,
                observed_value,
                expected_value,
                details_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                result_id,
                quality_run_id,
                asset_id,
                raw_batch_id,
                check["check_name"],
                check["check_status"],
                check["observed_value"],
                check["expected_value"],
                canonical_json(details),
            ),
        )
    return quality_run_id


def _validate_rows_in_requested_window(
    raw_rows: list[dict[str, object]],
    requested_start: str,
    requested_end: str,
) -> None:
    start = date.fromisoformat(requested_start)
    end = date.fromisoformat(requested_end)
    if end <= start:
        raise ValueError(
            "requested_end debe ser posterior a requested_start "
            "(end es exclusivo)"
        )
    outside = [
        str(row["trading_day"])
        for row in raw_rows
        if not start <= date.fromisoformat(str(row["trading_day"])) < end
    ]
    if outside:
        raise ValueError(
            "El proveedor devolvió filas fuera de [start, end): "
            f"{outside[:5]}"
        )


def _insert_batch_retrieval(
    conn: sqlite3.Connection,
    *,
    raw_batch_id: str,
    source_run_id: str | None,
    retrieved_at: str,
    request_json: str,
) -> tuple[str, bool]:
    identity = (
        f"run:{source_run_id}"
        if source_run_id is not None
        else f"time:{retrieved_at}"
    )
    retrieval_id = stable_id(
        "rpbr", raw_batch_id, identity
    )
    inserted = (
        conn.execute(
            """
            INSERT OR IGNORE INTO raw_price_batch_retrievals(
                batch_retrieval_id,
                raw_batch_id,
                source_run_id,
                retrieved_at,
                request_json
            )
            VALUES (?, ?, ?, ?, ?)
            """,
            (
                retrieval_id,
                raw_batch_id,
                source_run_id,
                retrieved_at,
                request_json,
            ),
        ).rowcount
        == 1
    )
    return retrieval_id, inserted


def _latest_price_observation(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    trading_day: str,
) -> dict[str, object] | None:
    row = conn.execute(
        """
        SELECT
            observation.price_observation_id,
            observation.price_bar_version_id,
            observation.observed_at,
            observation.observation_sequence,
            observation.state_revision_number
        FROM price_bar_observations AS observation
        WHERE observation.source_id = ?
          AND observation.asset_id = ?
          AND observation.interval = '1d'
          AND observation.trading_day = ?
        ORDER BY observation.observation_sequence DESC
        LIMIT 1
        """,
        (SOURCE_ID, asset_id, trading_day),
    ).fetchone()
    if row is None:
        return None
    return {
        "observation_id": str(row[0]),
        "version_id": str(row[1]),
        "observed_at": str(row[2]),
        "sequence": int(row[3]),
        "state_revision_number": int(row[4]),
    }


def _price_version_seen(
    conn: sqlite3.Connection,
    price_bar_version_id: str,
) -> bool:
    return (
        conn.execute(
            """
            SELECT 1
            FROM price_bar_observations
            WHERE price_bar_version_id = ?
            LIMIT 1
            """,
            (price_bar_version_id,),
        ).fetchone()
        is not None
    )


def _persist_price_observation(
    conn: sqlite3.Connection,
    *,
    raw_batch_id: str,
    batch_retrieval_id: str,
    asset_id: int,
    symbol: str,
    exchange: str,
    calendar_name: str,
    row: dict[str, object],
    retrieved: datetime,
) -> tuple[int, int]:
    session = row["session"]
    assert isinstance(session, SessionBounds)
    normalized_bar = {
        "trading_day": row["trading_day"],
        "exchange": exchange,
        "calendar_name": calendar_name,
        "bar_start_utc": session.open_utc.isoformat(),
        "bar_end_utc": session.close_utc.isoformat(),
        "open": row["open"],
        "high": row["high"],
        "low": row["low"],
        "close": row["close"],
        "volume": row["volume"],
    }
    normalized_json = canonical_json(normalized_bar)
    content_sha256 = hashlib.sha256(
        normalized_json.encode("utf-8")
    ).hexdigest()
    version_id = stable_id(
        "pbv",
        SOURCE_ID,
        asset_id,
        "1d",
        row["trading_day"],
        content_sha256,
    )
    version_inserted = conn.execute(
        """
        INSERT OR IGNORE INTO price_bar_versions(
            price_bar_version_id,
            first_raw_batch_id,
            first_batch_retrieval_id,
            source_id,
            asset_id,
            provider_symbol,
            interval,
            trading_day,
            exchange,
            calendar_name,
            bar_start_utc,
            bar_end_utc,
            first_observed_at,
            open,
            high,
            low,
            close,
            volume,
            adjusted_close,
            provider_row_number,
            bar_content_sha256,
            normalized_bar_json,
            batch_version,
            parser_version
        )
        VALUES (
            :version_id, :raw_batch_id, :retrieval_id, :source_id,
            :asset_id, :symbol, '1d', :trading_day, :exchange,
            :calendar_name, :bar_start, :bar_end, :observed_at,
            :open, :high, :low, :close, :volume, :adjusted_close,
            :row_number, :content_sha256, :normalized_json,
            :batch_version, :parser_version
        )
        """,
        {
            "version_id": version_id,
            "raw_batch_id": raw_batch_id,
            "retrieval_id": batch_retrieval_id,
            "source_id": SOURCE_ID,
            "asset_id": asset_id,
            "symbol": symbol.upper(),
            "trading_day": row["trading_day"],
            "exchange": exchange,
            "calendar_name": calendar_name,
            "bar_start": session.open_utc.isoformat(),
            "bar_end": session.close_utc.isoformat(),
            "observed_at": retrieved.isoformat(),
            "open": row["open"],
            "high": row["high"],
            "low": row["low"],
            "close": row["close"],
            "volume": row["volume"],
            "adjusted_close": row["adjusted_close"],
            "row_number": row["provider_row_number"],
            "content_sha256": content_sha256,
            "normalized_json": normalized_json,
            "batch_version": BATCH_VERSION,
            "parser_version": PARSER_VERSION,
        },
    ).rowcount

    previous = _latest_price_observation(
        conn,
        asset_id=asset_id,
        trading_day=str(row["trading_day"]),
    )
    if previous is None:
        kind = "initial_backfill"
        basis = "session_close_backfill_assumption"
        available_at = session.close_utc
        sequence = 1
        state_revision = 1
        previous_id = None
    else:
        if parse_utc(str(previous["observed_at"])) > retrieved:
            raise ValueError(
                "No se admite insertar una observación de precios "
                "anterior a la última ya persistida"
            )
        previous_id = str(previous["observation_id"])
        sequence = int(previous["sequence"]) + 1
        if previous["version_id"] == version_id:
            kind = "unchanged"
            basis = "retrieval_time_unchanged"
            state_revision = int(previous["state_revision_number"])
        else:
            state_revision = int(previous["state_revision_number"]) + 1
            if _price_version_seen(conn, version_id):
                kind = "reversion"
                basis = "retrieval_time_reversion"
            else:
                kind = "revision"
                basis = "retrieval_time_revision"
        available_at = max(session.close_utc, retrieved)

    observation_id = stable_id(
        "pbo",
        batch_retrieval_id,
        asset_id,
        "1d",
        row["trading_day"],
        row["provider_row_number"],
    )
    observation_inserted = conn.execute(
        """
        INSERT OR IGNORE INTO price_bar_observations(
            price_observation_id,
            price_bar_version_id,
            raw_batch_id,
            batch_retrieval_id,
            source_id,
            asset_id,
            provider_symbol,
            interval,
            trading_day,
            provider_row_number,
            observed_at,
            observed_adjusted_close,
            available_at,
            availability_basis,
            point_in_time_verified,
            observation_kind,
            observation_sequence,
            state_revision_number,
            previous_observation_id
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, '1d', ?, ?, ?, ?, ?, ?, 0, ?, ?, ?, ?
        )
        """,
        (
            observation_id,
            version_id,
            raw_batch_id,
            batch_retrieval_id,
            SOURCE_ID,
            asset_id,
            symbol.upper(),
            row["trading_day"],
            row["provider_row_number"],
            retrieved.isoformat(),
            row["adjusted_close"],
            available_at.isoformat(),
            basis,
            kind,
            sequence,
            state_revision,
            previous_id,
        ),
    ).rowcount
    return version_inserted, observation_inserted


def _latest_action_observation(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    trading_day: str,
    action_type: str,
) -> dict[str, object] | None:
    row = conn.execute(
        """
        SELECT
            observation.action_observation_id,
            observation.corporate_action_version_id,
            observation.observed_at,
            observation.observation_sequence,
            observation.state_revision_number,
            version.is_present
        FROM corporate_action_observations AS observation
        JOIN corporate_action_versions AS version
          ON version.corporate_action_version_id =
             observation.corporate_action_version_id
        WHERE observation.source_id = ?
          AND observation.asset_id = ?
          AND observation.effective_trading_day = ?
          AND observation.action_type = ?
        ORDER BY observation.observation_sequence DESC
        LIMIT 1
        """,
        (SOURCE_ID, asset_id, trading_day, action_type),
    ).fetchone()
    if row is None:
        return None
    return {
        "observation_id": str(row[0]),
        "version_id": str(row[1]),
        "observed_at": str(row[2]),
        "sequence": int(row[3]),
        "state_revision_number": int(row[4]),
        "is_present": bool(row[5]),
    }


def _action_version_seen(
    conn: sqlite3.Connection,
    corporate_action_version_id: str,
) -> bool:
    return (
        conn.execute(
            """
            SELECT 1
            FROM corporate_action_observations
            WHERE corporate_action_version_id = ?
            LIMIT 1
            """,
            (corporate_action_version_id,),
        ).fetchone()
        is not None
    )


def _persist_action_observation(
    conn: sqlite3.Connection,
    *,
    raw_batch_id: str,
    batch_retrieval_id: str,
    asset_id: int,
    symbol: str,
    row: dict[str, object],
    action_type: str,
    raw_value: float | None,
    retrieved: datetime,
) -> tuple[int, int, bool]:
    trading_day = str(row["trading_day"])
    previous = _latest_action_observation(
        conn,
        asset_id=asset_id,
        trading_day=trading_day,
        action_type=action_type,
    )
    is_present = raw_value is not None and raw_value != 0.0
    if not is_present and previous is None:
        return 0, 0, False

    session = row["session"]
    assert isinstance(session, SessionBounds)
    action_time = session.open_utc
    normalized_action = {
        "action_type": action_type,
        "effective_trading_day": trading_day,
        "action_time_utc": action_time.isoformat(),
        "is_present": is_present,
        "raw_value": raw_value if is_present else None,
    }
    normalized_json = canonical_json(normalized_action)
    content_sha256 = hashlib.sha256(
        normalized_json.encode("utf-8")
    ).hexdigest()
    version_id = stable_id(
        "cav",
        SOURCE_ID,
        asset_id,
        action_type,
        trading_day,
        content_sha256,
    )
    version_inserted = conn.execute(
        """
        INSERT OR IGNORE INTO corporate_action_versions(
            corporate_action_version_id,
            first_raw_batch_id,
            first_batch_retrieval_id,
            source_id,
            asset_id,
            provider_symbol,
            action_type,
            effective_trading_day,
            action_time_utc,
            is_present,
            raw_value,
            currency,
            action_content_sha256,
            provider_row_number,
            normalized_action_json,
            batch_version,
            parser_version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            version_id,
            raw_batch_id,
            batch_retrieval_id,
            SOURCE_ID,
            asset_id,
            symbol.upper(),
            action_type,
            trading_day,
            action_time.isoformat(),
            int(is_present),
            raw_value if is_present else None,
            None,
            content_sha256,
            row["provider_row_number"],
            normalized_json,
            BATCH_VERSION,
            PARSER_VERSION,
        ),
    ).rowcount

    if previous is None:
        kind = "initial_observation"
        sequence = 1
        state_revision = 1
        previous_id = None
    else:
        if parse_utc(str(previous["observed_at"])) > retrieved:
            raise ValueError(
                "No se admite insertar una acción corporativa anterior "
                "a la última ya persistida"
            )
        previous_id = str(previous["observation_id"])
        sequence = int(previous["sequence"]) + 1
        if previous["version_id"] == version_id:
            kind = "unchanged"
            state_revision = int(previous["state_revision_number"])
        else:
            state_revision = int(previous["state_revision_number"]) + 1
            if not is_present:
                kind = "retraction"
            elif _action_version_seen(conn, version_id):
                kind = "reversion"
            else:
                kind = "revision"

    observation_id = stable_id(
        "cao",
        batch_retrieval_id,
        asset_id,
        action_type,
        trading_day,
        row["provider_row_number"],
    )
    observation_inserted = conn.execute(
        """
        INSERT OR IGNORE INTO corporate_action_observations(
            action_observation_id,
            corporate_action_version_id,
            raw_batch_id,
            batch_retrieval_id,
            source_id,
            asset_id,
            provider_symbol,
            action_type,
            effective_trading_day,
            announcement_available_at,
            observed_at,
            available_at,
            availability_basis,
            observation_kind,
            observation_sequence,
            state_revision_number,
            previous_observation_id,
            provider_row_number
        )
        VALUES (
            ?, ?, ?, ?, ?, ?, ?, ?, ?, NULL, ?, ?,
            'retrieval_time_no_announcement', ?, ?, ?, ?, ?
        )
        """,
        (
            observation_id,
            version_id,
            raw_batch_id,
            batch_retrieval_id,
            SOURCE_ID,
            asset_id,
            symbol.upper(),
            action_type,
            trading_day,
            retrieved.isoformat(),
            retrieved.isoformat(),
            kind,
            sequence,
            state_revision,
            previous_id,
            row["provider_row_number"],
        ),
    ).rowcount
    return version_inserted, observation_inserted, True


def persist_provider_frame(
    conn: sqlite3.Connection,
    store: RawPriceStore,
    *,
    asset_id: int,
    symbol: str,
    exchange: str,
    requested_start: str,
    requested_end: str,
    retrieved_at: str,
    provider_library_version: str,
    frame: Any,
    source_run_id: str | None = None,
    session_resolver: Any | None = None,
) -> PersistResult:
    ensure_contract(conn)
    retrieved = parse_utc(retrieved_at)
    normalized_exchange = canonical_exchange(exchange)
    expected_calendar = calendar_name_for_exchange(normalized_exchange)
    resolver = session_resolver or ExchangeCalendarResolver(
        normalized_exchange,
        start=requested_start,
        end=requested_end,
    )
    calendar_name = str(resolver.calendar_name)
    if calendar_name != expected_calendar:
        raise ValueError(
            f"Calendario {calendar_name!r} incompatible con "
            f"exchange {normalized_exchange!r}"
        )

    payload, raw_rows = canonical_provider_payload(
        frame,
        symbol=symbol,
        requested_start=requested_start,
        requested_end=requested_end,
        provider_library_version=provider_library_version,
        exchange=normalized_exchange,
        calendar_name=calendar_name,
    )
    if not raw_rows:
        raise ValueError(
            "Yahoo Finance no devolvió filas para el rango solicitado"
        )
    _validate_rows_in_requested_window(
        raw_rows, requested_start, requested_end
    )

    storage_path, raw_sha256, byte_length = store.write(symbol, payload)
    raw_batch_id = stable_id(
        "rpb",
        SOURCE_ID,
        asset_id,
        symbol.upper(),
        raw_sha256,
        BATCH_VERSION,
    )
    request_json = canonical_json(
        {
            "symbol": symbol.upper(),
            "start": requested_start,
            "end": requested_end,
            "interval": "1d",
            "auto_adjust": False,
            "actions": True,
            "repair": False,
            "keepna": True,
            "timeout_seconds": PROVIDER_TIMEOUT_SECONDS,
            "exceptions_visible": True,
            "exception_visibility_contract": (
                EXCEPTION_VISIBILITY_CONTRACT
            ),
            "exchange": normalized_exchange,
            "calendar_name": calendar_name,
        }
    )
    batch_inserted = (
        conn.execute(
            """
            INSERT OR IGNORE INTO raw_price_batches(
                raw_batch_id,
                source_id,
                source_run_id,
                asset_id,
                provider_symbol,
                exchange,
                calendar_name,
                interval,
                requested_start,
                requested_end,
                retrieved_at,
                lineage_kind,
                is_exact_http_response,
                provider_library_name,
                provider_library_version,
                request_json,
                raw_sha256,
                storage_path,
                byte_length,
                row_count,
                batch_version,
                parser_version
            )
            VALUES (?, ?, ?, ?, ?, ?, ?, '1d', ?, ?, ?,
                    'provider_library_output', 0, 'yfinance', ?, ?, ?, ?,
                    ?, ?, ?, ?)
            """,
            (
                raw_batch_id,
                SOURCE_ID,
                source_run_id,
                asset_id,
                symbol.upper(),
                normalized_exchange,
                calendar_name,
                requested_start,
                requested_end,
                retrieved.isoformat(),
                provider_library_version,
                request_json,
                raw_sha256,
                str(storage_path),
                byte_length,
                len(raw_rows),
                BATCH_VERSION,
                PARSER_VERSION,
            ),
        ).rowcount
        == 1
    )

    batch_retrieval_id, batch_retrieval_inserted = (
        _insert_batch_retrieval(
            conn,
            raw_batch_id=raw_batch_id,
            source_run_id=source_run_id,
            retrieved_at=retrieved.isoformat(),
            request_json=request_json,
        )
    )

    identifier_id = stable_id(
        "aih",
        asset_id,
        SOURCE_ID,
        "provider_symbol",
        symbol.upper(),
        "unknown",
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO asset_identifier_history(
            identifier_history_id,
            asset_id,
            identifier_type,
            identifier_value,
            source_id,
            valid_from,
            valid_to,
            available_at,
            retrieved_at,
            is_primary,
            metadata_json
        )
        VALUES (?, ?, 'provider_symbol', ?, ?, NULL, NULL, ?, ?, 1, ?)
        """,
        (
            identifier_id,
            asset_id,
            symbol.upper(),
            SOURCE_ID,
            retrieved.isoformat(),
            retrieved.isoformat(),
            canonical_json(
                {
                    "provider": SOURCE_ID,
                    "validity_start_known": False,
                }
            ),
        ),
    )

    rows = [
        _normalized_price_row(raw_row, resolver, retrieved)
        for raw_row in raw_rows
    ]
    bar_versions_inserted = 0
    bar_observations_inserted = 0
    actions_discovered = 0
    action_versions_inserted = 0
    action_observations_inserted = 0
    bars_without_session = 0
    bars_incomplete_session = 0
    day_counts = Counter(str(row["trading_day"]) for row in rows)
    duplicate_days = {
        trading_day
        for trading_day, count in day_counts.items()
        if count > 1
    }
    bars_duplicate_trading_day = 0

    for row in rows:
        if str(row["trading_day"]) in duplicate_days:
            bars_duplicate_trading_day += 1
            continue
        session = row["session"]
        if session is None:
            bars_without_session += 1
            continue
        if not row["is_complete"]:
            bars_incomplete_session += 1
            continue

        version_inserted, observation_inserted = (
            _persist_price_observation(
                conn,
                raw_batch_id=raw_batch_id,
                batch_retrieval_id=batch_retrieval_id,
                asset_id=asset_id,
                symbol=symbol,
                exchange=normalized_exchange,
                calendar_name=calendar_name,
                row=row,
                retrieved=retrieved,
            )
        )
        bar_versions_inserted += version_inserted
        bar_observations_inserted += observation_inserted

        for action_type, action_value in _action_observations(row):
            (
                action_version_inserted,
                action_observation_inserted,
                processed,
            ) = _persist_action_observation(
                conn,
                raw_batch_id=raw_batch_id,
                batch_retrieval_id=batch_retrieval_id,
                asset_id=asset_id,
                symbol=symbol,
                row=row,
                action_type=action_type,
                raw_value=action_value,
                retrieved=retrieved,
            )
            if processed:
                actions_discovered += 1
                action_versions_inserted += action_version_inserted
                action_observations_inserted += (
                    action_observation_inserted
                )

    quality_run_id = _persist_quality(
        conn,
        raw_batch_id=raw_batch_id,
        batch_retrieval_id=batch_retrieval_id,
        asset_id=asset_id,
        rows=rows,
        requested_start=requested_start,
        requested_end=requested_end,
        session_resolver=resolver,
        retrieved=retrieved,
        checked_at=retrieved.isoformat(),
    )
    valid_bars = (
        len(rows)
        - bars_without_session
        - bars_incomplete_session
        - bars_duplicate_trading_day
    )
    return PersistResult(
        raw_batch_id=raw_batch_id,
        raw_sha256=raw_sha256,
        batch_inserted=batch_inserted,
        batch_retrieval_id=batch_retrieval_id,
        batch_retrieval_inserted=batch_retrieval_inserted,
        bars_discovered=valid_bars,
        bars_inserted=bar_versions_inserted,
        bars_existing=valid_bars - bar_versions_inserted,
        bar_observations_inserted=bar_observations_inserted,
        bar_observations_existing=(
            valid_bars - bar_observations_inserted
        ),
        bars_without_session=bars_without_session,
        bars_incomplete_session=bars_incomplete_session,
        bars_duplicate_trading_day=bars_duplicate_trading_day,
        actions_discovered=actions_discovered,
        actions_inserted=action_versions_inserted,
        actions_existing=actions_discovered - action_versions_inserted,
        action_observations_inserted=action_observations_inserted,
        action_observations_existing=(
            actions_discovered - action_observations_inserted
        ),
        quality_run_id=quality_run_id,
    )


def resolve_asset(
    conn: sqlite3.Connection,
    ticker: str,
    exchange_override: str | None,
) -> tuple[int, str, str]:
    columns = {
        row[1] for row in conn.execute("PRAGMA table_info(assets)")
    }
    if "exchange" not in columns:
        raise RuntimeError("La tabla assets no contiene la columna exchange")
    row = conn.execute(
        """
        SELECT asset_id, ticker, exchange
        FROM assets
        WHERE UPPER(ticker) = UPPER(?)
        """,
        (ticker,),
    ).fetchone()
    if row is None:
        raise ValueError(f"El ticker {ticker!r} no existe en assets")
    exchange_value = (exchange_override or row[2] or "").strip().upper()
    if not exchange_value:
        raise ValueError(
            f"El activo {row[1]} no tiene exchange; indique --exchange"
        )
    exchange = canonical_exchange(exchange_value)
    return int(row[0]), str(row[1]), exchange


def validate_raw_root(raw_root: Path) -> Path:
    if raw_root.name.casefold() == "prices":
        raise ValueError(
            "--raw-root debe apuntar a la raíz data/raw; "
            "RawPriceStore agrega prices/yahoo_finance/daily"
        )
    return raw_root


def validate_pilot_window(
    requested_start: str,
    requested_end: str,
    max_days: int,
) -> tuple[date, date]:
    start = date.fromisoformat(requested_start)
    end = date.fromisoformat(requested_end)
    if end <= start:
        raise ValueError(
            "--end debe ser posterior a --start (end es exclusivo)"
        )
    if max_days <= 0 or max_days > ABSOLUTE_MAX_DAYS:
        raise ValueError(
            f"--max-days debe estar entre 1 y {ABSOLUTE_MAX_DAYS}"
        )
    span = (end - start).days
    if span > max_days:
        raise ValueError(
            f"Rango de {span} días excede el límite piloto de {max_days}"
        )
    return start, end


def _configure_provider_exception_visibility(
    provider_module: Any,
) -> bool:
    try:
        debug_config = provider_module.config.debug
        debug_config.hide_exceptions = False
        return debug_config.hide_exceptions is False
    except (AttributeError, TypeError, ValueError):
        return False


def fetch_yahoo_daily(
    symbol: str,
    requested_start: str,
    requested_end: str,
    *,
    ticker_factory: Callable[[str], Any] | None = None,
    provider_library_version: str | None = None,
    provider_module: Any | None = None,
) -> tuple[Any, str]:
    if ticker_factory is None:
        if provider_module is None:
            import yfinance as provider_module
        ticker_factory = provider_module.Ticker
        provider_library_version = provider_module.__version__

    history_kwargs: dict[str, object] = {
        "start": requested_start,
        "end": requested_end,
        "interval": "1d",
        "auto_adjust": False,
        "actions": True,
        "repair": False,
        "keepna": True,
        "timeout": PROVIDER_TIMEOUT_SECONDS,
    }
    config_controls_errors = (
        provider_module is not None
        and _configure_provider_exception_visibility(provider_module)
    )
    if not config_controls_errors:
        history_kwargs["raise_errors"] = True

    frame = ticker_factory(symbol).history(**history_kwargs)
    return frame, provider_library_version or "unknown"


def run_pilot(
    *,
    db: Path,
    raw_root: Path,
    ticker: str,
    requested_start: str,
    requested_end: str,
    exchange_override: str | None,
    max_days: int,
) -> dict[str, object]:
    validate_pilot_window(requested_start, requested_end, max_days)
    raw_root = validate_raw_root(raw_root)
    run_id = str(uuid.uuid4())
    started_at = utc_now()

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_contract(conn)
        asset_id, canonical_ticker, exchange = resolve_asset(
            conn, ticker, exchange_override
        )
        checkpoint_before = canonical_json(
            {
                "ticker": canonical_ticker,
                "start": requested_start,
                "end": requested_end,
                "exchange": exchange,
                "interval": "1d",
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
            VALUES (?, ?, 'yahoo_daily_pilot_v1', ?, 'running', ?)
            """,
            (run_id, SOURCE_ID, started_at, checkpoint_before),
        )
        conn.commit()

        try:
            frame, provider_version = fetch_yahoo_daily(
                canonical_ticker,
                requested_start,
                requested_end,
            )
            result = persist_provider_frame(
                conn,
                RawPriceStore(raw_root),
                asset_id=asset_id,
                symbol=canonical_ticker,
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
                    canonical_json(
                        {
                            "raw_batch_id": result.raw_batch_id,
                            "batch_retrieval_id": result.batch_retrieval_id,
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
                    canonical_json(
                        {
                            "error_type": type(error).__name__,
                            "message": str(error),
                        }
                    ),
                    run_id,
                ),
            )
            conn.commit()
            raise


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Piloto causal de precios diarios Yahoo Finance"
    )
    parser.add_argument("--ticker", required=True)
    parser.add_argument(
        "--start", required=True, dest="requested_start"
    )
    parser.add_argument(
        "--end",
        required=True,
        dest="requested_end",
        help="Fecha final exclusiva",
    )
    parser.add_argument(
        "--exchange",
        help="Obligatorio si assets.exchange está vacío; ej. XNAS o XNYS",
    )
    parser.add_argument(
        "--max-days", type=int, default=DEFAULT_MAX_DAYS
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument(
        "--raw-root", type=Path, default=DEFAULT_RAW_ROOT
    )
    args = parser.parse_args()
    result = run_pilot(
        db=args.db,
        raw_root=args.raw_root,
        ticker=args.ticker,
        requested_start=args.requested_start,
        requested_end=args.requested_end,
        exchange_override=args.exchange,
        max_days=args.max_days,
    )
    print(json.dumps(result, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import gzip
import hashlib
import http.client
import json
import os
import re
import socket
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
import zlib
from dataclasses import dataclass, field
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from html.parser import HTMLParser
from pathlib import Path, PurePosixPath
from typing import Callable, Iterable, Protocol
from urllib.parse import parse_qs, quote, unquote, urljoin, urlparse

from ingestion.events.sec_edgar_v2 import (
    SOURCE_ID,
    normalize_cik,
    validate_user_agent,
)


DOCUMENT_VERSION = "sec_filing_documents_v0.4.0"
INVENTORY_PARSER_VERSION = "sec_filing_index_html_v0.4.0"
DEFAULT_DB = Path("data/database/market_data_v2.db")
DEFAULT_RAW_ROOT = Path("data/raw")
DEFAULT_RATE_LIMIT = 5.0
DEFAULT_MAX_FILINGS = 5
DEFAULT_MAX_FILES_PER_FILING = 20
DEFAULT_MAX_FILE_BYTES = 25 * 1024 * 1024
DEFAULT_MAX_INDEX_BYTES = 5 * 1024 * 1024
DEFAULT_MAX_TOTAL_BYTES = 100 * 1024 * 1024
DEFAULT_MAX_RETRY_AFTER_SECONDS = 30.0
MAX_INVENTORY_ROWS = 2_000
RETRYABLE_HTTP_CODES = {429, 500, 502, 503, 504}
ALLOWED_SEC_HOSTS = {"sec.gov", "www.sec.gov", "data.sec.gov"}
ACCESSION_PATTERN = re.compile(r"^[0-9]{10}-[0-9]{2}-[0-9]{6}$")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


class SecArchiveError(RuntimeError):
    pass


class ResponseTooLarge(SecArchiveError):
    pass


class RawIntegrityError(SecArchiveError):
    pass


EXPECTED_OPERATIONAL_ERRORS = (SecArchiveError, OSError)


@dataclass(frozen=True)
class SecResponse:
    requested_url: str
    final_url: str
    payload: bytes
    retrieved_at: str
    status: int = 200
    content_type: str | None = None
    transport_encoding: str | None = None
    last_modified: str | None = None
    headers: dict[str, str] = field(default_factory=dict)


class SecByteClient(Protocol):
    def get_bytes(self, url: str, *, max_bytes: int) -> SecResponse:
        ...


@dataclass(frozen=True)
class FilingRecord:
    raw_document_id: str
    cik: str
    accession_number: str
    form: str
    acceptance_datetime: str
    primary_document: str | None
    primary_doc_description: str | None


@dataclass(frozen=True)
class InventoryFile:
    sequence_number: str
    document_name: str
    document_type: str | None
    description: str | None
    declared_size_bytes: int | None
    table_section: str
    is_primary: bool = False


@dataclass(frozen=True)
class StoredPayload:
    path: Path
    sha256: str
    byte_length: int


@dataclass(frozen=True)
class PersistedRaw:
    raw_document_id: str
    inserted: bool
    revision_observed: bool
    sha256: str


@dataclass(frozen=True)
class FetchedFile:
    item: InventoryFile
    selection_reason: str
    attempted_at: str
    response: SecResponse | None = None
    error_message: str | None = None


@dataclass
class DownloadStats:
    documents_discovered: int = 0
    documents_inserted: int = 0
    documents_existing: int = 0
    revisions_observed: int = 0
    indexes_inserted: int = 0
    files_downloaded: int = 0
    files_skipped: int = 0
    errors: list[dict] = field(default_factory=list)

    def merge(self, other: "DownloadStats") -> None:
        self.documents_discovered += other.documents_discovered
        self.documents_inserted += other.documents_inserted
        self.documents_existing += other.documents_existing
        self.revisions_observed += other.revisions_observed
        self.indexes_inserted += other.indexes_inserted
        self.files_downloaded += other.files_downloaded
        self.files_skipped += other.files_skipped
        self.errors.extend(other.errors)


class ByteBudget:
    def __init__(self, maximum: int) -> None:
        if maximum <= 0:
            raise ValueError("El presupuesto total debe ser mayor que cero")
        self.maximum = maximum
        self.used = 0

    @property
    def remaining(self) -> int:
        return self.maximum - self.used

    def consume(self, amount: int) -> None:
        if amount < 0 or amount > self.remaining:
            raise ResponseTooLarge(
                f"El payload excede el presupuesto restante de {self.remaining} bytes"
            )
        self.used += amount


def validate_accession(value: str) -> str:
    normalized = value.strip()
    if not ACCESSION_PATTERN.fullmatch(normalized):
        raise ValueError(f"Accession SEC inválido: {value!r}")
    return normalized


def safe_document_name(value: str) -> str:
    decoded = unquote(value.strip())
    if not decoded or "\x00" in decoded or "\\" in decoded:
        raise ValueError(f"Nombre de documento SEC inválido: {value!r}")
    path = PurePosixPath(decoded)
    if path.is_absolute() or any(part in {"", ".", ".."} for part in path.parts):
        raise ValueError(f"Ruta de documento SEC insegura: {value!r}")
    return "/".join(path.parts)


def archive_directory_url(cik: str, accession_number: str) -> str:
    normalized_cik = str(int(normalize_cik(cik)))
    accession = validate_accession(accession_number)
    accession_path = accession.replace("-", "")
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{normalized_cik}/{accession_path}"
    )


def archive_index_url(cik: str, accession_number: str) -> str:
    accession = validate_accession(accession_number)
    return f"{archive_directory_url(cik, accession)}/{accession}-index.html"


def archive_file_url(
    cik: str,
    accession_number: str,
    document_name: str,
) -> str:
    safe_name = safe_document_name(document_name)
    encoded_path = "/".join(quote(part, safe="") for part in safe_name.split("/"))
    return f"{archive_directory_url(cik, accession_number)}/{encoded_path}"


def _normalized_last_modified(value: str | None) -> str | None:
    if not value:
        return None
    try:
        parsed = parsedate_to_datetime(value)
    except (TypeError, ValueError):
        return None
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _retry_after_seconds(value: str | None) -> float | None:
    if not value:
        return None
    try:
        return max(0.0, float(value))
    except ValueError:
        try:
            parsed = parsedate_to_datetime(value)
        except (TypeError, ValueError):
            return None
        if parsed.tzinfo is None:
            parsed = parsed.replace(tzinfo=timezone.utc)
        return max(
            0.0,
            (parsed.astimezone(timezone.utc) - datetime.now(timezone.utc)).total_seconds(),
        )


def _decode_gzip_limited(payload: bytes, maximum: int) -> bytes:
    if maximum < 0:
        raise ValueError("maximum no puede ser negativo")
    try:
        decoder = zlib.decompressobj(16 + zlib.MAX_WBITS)
        output = bytearray()
        for offset in range(0, len(payload), 64 * 1024):
            pending = payload[offset : offset + 64 * 1024]
            while pending:
                remaining_with_sentinel = maximum + 1 - len(output)
                if remaining_with_sentinel <= 0:
                    raise ResponseTooLarge(
                        f"Respuesta SEC descomprimida excede {maximum} bytes"
                    )
                output.extend(
                    decoder.decompress(
                        pending,
                        remaining_with_sentinel,
                    )
                )
                if len(output) > maximum:
                    raise ResponseTooLarge(
                        f"Respuesta SEC descomprimida excede {maximum} bytes"
                    )
                pending = decoder.unconsumed_tail
        if not decoder.eof:
            raise SecArchiveError("Respuesta SEC gzip truncada o inválida")
        if decoder.unused_data:
            raise SecArchiveError(
                "Respuesta SEC gzip contiene datos concatenados"
            )
        return bytes(output)
    except zlib.error as error:
        raise SecArchiveError("Respuesta SEC gzip inválida") from error


def _validate_sec_url(url: str) -> None:
    parsed = urlparse(url)
    if (
        parsed.scheme != "https"
        or parsed.username is not None
        or parsed.password is not None
        or (parsed.hostname or "").lower() not in ALLOWED_SEC_HOSTS
    ):
        raise ValueError(f"URL fuera de SEC no permitida: {url}")


class _SecRedirectHandler(urllib.request.HTTPRedirectHandler):
    def redirect_request(
        self,
        req,
        fp,
        code,
        msg,
        headers,
        newurl,
    ):
        target = urljoin(req.full_url, newurl)
        _validate_sec_url(target)
        return super().redirect_request(req, fp, code, msg, headers, target)


class SecArchiveClient:
    def __init__(
        self,
        user_agent: str,
        *,
        rate_limit_per_second: float = DEFAULT_RATE_LIMIT,
        timeout_seconds: float = 30.0,
        retries: int = 3,
        sleep_fn: Callable[[float], None] = time.sleep,
        max_retry_after_seconds: float = (
            DEFAULT_MAX_RETRY_AFTER_SECONDS
        ),
        monotonic_fn: Callable[[], float] = time.monotonic,
        opener: object | None = None,
    ) -> None:
        if rate_limit_per_second <= 0 or rate_limit_per_second > 5:
            raise ValueError("SEC rate limit debe estar entre 0 y 5 req/s")
        if timeout_seconds <= 0:
            raise ValueError("timeout_seconds debe ser mayor que cero")
        if max_retry_after_seconds <= 0:
            raise ValueError(
                "max_retry_after_seconds debe ser mayor que cero"
            )
        if retries < 0 or retries > 5:
            raise ValueError("retries debe estar entre 0 y 5")
        self.user_agent = validate_user_agent(user_agent)
        self.minimum_interval = 1.0 / rate_limit_per_second
        self.max_retry_after_seconds = max_retry_after_seconds
        self.timeout_seconds = timeout_seconds
        self.retries = retries
        self.sleep_fn = sleep_fn
        self.monotonic_fn = monotonic_fn
        self.opener = opener or urllib.request.build_opener(_SecRedirectHandler())
        self._last_request_started: float | None = None

    @staticmethod
    def _validate_url(url: str) -> None:
        _validate_sec_url(url)

    def _respect_rate_limit(self) -> None:
        now = self.monotonic_fn()
        if self._last_request_started is not None:
            remaining = self.minimum_interval - (now - self._last_request_started)
            if remaining > 0:
                self.sleep_fn(remaining)
        self._last_request_started = self.monotonic_fn()

    def _read_response(self, response: object, maximum: int) -> bytes:
        content_length = response.headers.get("Content-Length")
        if content_length:
            try:
                declared = int(content_length)
            except ValueError:
                declared = None
            if declared is not None and declared > maximum:
                raise ResponseTooLarge(
                    f"Content-Length SEC {declared} excede {maximum} bytes"
                )

        body = bytearray()
        while True:
            chunk = response.read(min(64 * 1024, maximum + 1 - len(body)))
            if not chunk:
                break
            body.extend(chunk)
            if len(body) > maximum:
                raise ResponseTooLarge(
                    f"Respuesta SEC excede {maximum} bytes"
                )

        payload = bytes(body)
        encoding = (response.headers.get("Content-Encoding") or "").lower()
        if encoding == "gzip":
            payload = _decode_gzip_limited(payload, maximum)
        elif encoding not in {"", "identity"}:
            raise SecArchiveError(
                f"Content-Encoding SEC no soportado: {encoding}"
            )
        return payload

    def get_bytes(self, url: str, *, max_bytes: int) -> SecResponse:
        if max_bytes <= 0:
            raise ResponseTooLarge("No queda presupuesto para otra respuesta SEC")
        self._validate_url(url)

        for attempt in range(self.retries + 1):
            self._respect_rate_limit()
            request = urllib.request.Request(
                url,
                headers={
                    "User-Agent": self.user_agent,
                    "Accept": "text/html,application/xhtml+xml,application/xml,"
                    "text/plain,application/pdf,*/*",
                    "Accept-Encoding": "identity",
                },
            )
            try:
                with self.opener.open(
                    request,
                    timeout=self.timeout_seconds,
                ) as response:
                    final_url = response.geturl()
                    self._validate_url(final_url)
                    payload = self._read_response(response, max_bytes)
                    retrieved_at = utc_now()
                    content_type = response.headers.get("Content-Type")
                    if content_type:
                        content_type = content_type.split(";", 1)[0].strip()
                    headers = {
                        key.lower(): value
                        for key, value in response.headers.items()
                        if key.lower()
                        in {
                            "content-type",
                            "content-length",
                            "content-encoding",
                            "etag",
                            "last-modified",
                            "date",
                        }
                    }
                    return SecResponse(
                        requested_url=url,
                        final_url=final_url,
                        payload=payload,
                        retrieved_at=retrieved_at,
                        status=int(getattr(response, "status", 200)),
                        content_type=content_type,
                        transport_encoding=response.headers.get(
                            "Content-Encoding"
                        ),
                        last_modified=_normalized_last_modified(
                            response.headers.get("Last-Modified")
                        ),
                        headers=headers,
                    )
            except urllib.error.HTTPError as error:
                if error.code not in RETRYABLE_HTTP_CODES or attempt >= self.retries:
                    raise SecArchiveError(
                        f"SEC respondió HTTP {error.code} para {url}"
                    ) from error
                retry_after = _retry_after_seconds(
                    error.headers.get("Retry-After") if error.headers else None
                )
                if (
                    retry_after is not None
                    and retry_after > self.max_retry_after_seconds
                ):
                    raise SecArchiveError(
                        f"SEC solicitó Retry-After={retry_after:.3f}s; "
                        "supera el máximo configurado y el run debe posponerse"
                    ) from error
                delay = (
                    retry_after
                    if retry_after is not None
                    else min(self.max_retry_after_seconds, 2.0**attempt)
                )
                self.sleep_fn(delay)
            except (
                urllib.error.URLError,
                http.client.IncompleteRead,
                http.client.HTTPException,
                socket.timeout,
                TimeoutError,
                ConnectionError,
                OSError,
            ) as error:
                if attempt >= self.retries:
                    raise SecArchiveError(
                        f"No se pudo leer la respuesta SEC para {url}: {error}"
                    ) from error
                self.sleep_fn(min(self.max_retry_after_seconds, 2.0**attempt))

        raise AssertionError("Bucle de reintentos SEC terminó inesperadamente")


class ContentAddressedRawStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    def _destination(
        self,
        *,
        category: str,
        year: str,
        cik: str,
        accession_number: str,
        digest: str,
    ) -> Path:
        if not re.fullmatch(r"[a-z_]+", category):
            raise ValueError(f"Categoría raw inválida: {category!r}")
        destination = (
            self.root
            / "sec"
            / "archive"
            / year
            / normalize_cik(cik)
            / validate_accession(accession_number).replace("-", "")
            / category
            / f"{digest}.bin.gz"
        )
        root_resolved = self.root.resolve()
        destination.resolve().relative_to(root_resolved)
        return destination

    @staticmethod
    def _verify_existing(
        destination: Path,
        expected_sha256: str,
        expected_length: int,
    ) -> None:
        digest = hashlib.sha256()
        length = 0
        try:
            with gzip.open(destination, "rb") as stream:
                while True:
                    chunk = stream.read(64 * 1024)
                    if not chunk:
                        break
                    length += len(chunk)
                    digest.update(chunk)
                    if length > expected_length:
                        break
        except (OSError, EOFError) as error:
            raise RawIntegrityError(
                f"Raw gzip corrupto: {destination}"
            ) from error
        if length != expected_length or digest.hexdigest() != expected_sha256:
            raise RawIntegrityError(
                f"Raw existente no coincide con su ruta content-addressed: "
                f"{destination}"
            )

    def write(
        self,
        *,
        category: str,
        year: str,
        cik: str,
        accession_number: str,
        payload: bytes,
    ) -> StoredPayload:
        digest = hashlib.sha256(payload).hexdigest()
        destination = self._destination(
            category=category,
            year=year,
            cik=cik,
            accession_number=accession_number,
            digest=digest,
        )
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            self._verify_existing(destination, digest, len(payload))
        else:
            compressed = gzip.compress(payload, compresslevel=6, mtime=0)
            temporary = destination.with_name(
                f".{destination.name}.{uuid.uuid4().hex}.tmp"
            )
            try:
                with temporary.open("xb") as stream:
                    stream.write(compressed)
                    stream.flush()
                    os.fsync(stream.fileno())
                os.replace(temporary, destination)
            finally:
                if temporary.exists():
                    temporary.unlink()

        return StoredPayload(
            path=destination,
            sha256=digest,
            byte_length=len(payload),
        )


class _FilingTableParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.active_table = False
        self.table_files_started = 0
        self.table_files_closed = 0
        self.table_depth = 0
        self.table_section = ""
        self.current_row: list[tuple[str, str | None]] | None = None
        self.current_cell_text: list[str] | None = None
        self.current_cell_href: str | None = None
        self.rows: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        attributes = {key.lower(): value or "" for key, value in attrs}
        if tag.lower() == "table":
            if self.active_table:
                self.table_depth += 1
            else:
                classes = attributes.get("class", "").split()
                if "tableFile" in classes:
                    self.active_table = True
                    self.table_files_started += 1
                    self.table_depth = 1
                    self.table_section = attributes.get("summary", "")
            return
        if not self.active_table:
            return
        if tag.lower() == "tr":
            self.current_row = []
        elif tag.lower() in {"td", "th"} and self.current_row is not None:
            self.current_cell_text = []
            self.current_cell_href = None
        elif (
            tag.lower() == "a"
            and self.current_cell_text is not None
            and attributes.get("href")
        ):
            self.current_cell_href = attributes["href"]

    def handle_data(self, data: str) -> None:
        if self.current_cell_text is not None:
            self.current_cell_text.append(data)

    def handle_endtag(self, tag: str) -> None:
        lowered = tag.lower()
        if not self.active_table:
            return
        if lowered in {"td", "th"} and self.current_cell_text is not None:
            text = " ".join("".join(self.current_cell_text).split())
            assert self.current_row is not None
            self.current_row.append((text, self.current_cell_href))
            self.current_cell_text = None
            self.current_cell_href = None
        elif lowered == "tr" and self.current_row is not None:
            if self.current_row:
                self.rows.append((self.table_section, self.current_row))
            self.current_row = None
        elif lowered == "table":
            self.table_depth -= 1
            if self.table_depth == 0:
                self.active_table = False
                self.table_section = ""

                self.table_files_closed += 1

def _canonical_document_path(target: str) -> str:
    decoded_path = unquote(urlparse(target).path).rstrip("/")
    parts = [part for part in PurePosixPath(decoded_path).parts if part != "/"]
    lowered = [part.casefold() for part in parts]
    archive_prefix = ["archives", "edgar", "data"]
    candidate = ""
    for offset in range(0, len(parts) - 2):
        if lowered[offset : offset + 3] != archive_prefix:
            continue
        # Archives/edgar/data/{cik}/{accession}/{document path...}
        document_offset = offset + 5
        if document_offset < len(parts):
            candidate = "/".join(parts[document_offset:])
        break
    if not candidate and decoded_path and not decoded_path.startswith("/"):
        candidate = decoded_path
    if not candidate and parts:
        candidate = parts[-1]
    if candidate.casefold() in {"ix", "ixviewer", "ix.html"}:
        return ""
    return candidate


def _document_name_from_cell(text: str, href: str | None) -> str:
    candidate = ""
    if href:
        parsed = urlparse(href)
        query_document = parse_qs(parsed.query).get("doc")
        target = query_document[0] if query_document else parsed.path
        candidate = _canonical_document_path(target)
    if not candidate:
        candidate = text.strip()
    return safe_document_name(candidate)


def _parse_size(value: str) -> int | None:
    normalized = value.replace(",", "").strip()
    if not normalized:
        return None
    if not normalized.isdigit():
        return None
    return int(normalized)


def parse_filing_index(
    payload: bytes,
    *,
    max_rows: int = MAX_INVENTORY_ROWS,
) -> list[InventoryFile]:
    parser = _FilingTableParser()
    parser.feed(payload.decode("utf-8", errors="replace"))
    parser.close()
    if (
        parser.active_table
        or parser.table_files_started == 0
        or parser.table_files_closed != parser.table_files_started
    ):
        raise SecArchiveError(
            "Índice SEC sin una tableFile completa; posible bloqueo o truncado"
        )

    results: list[InventoryFile] = []
    seen: set[tuple[str, str]] = set()
    for section, cells in parser.rows:
        if not cells:
            continue
        if cells[0][0].strip().lower() in {"seq", "sequence"}:
            continue
        if len(cells) < 4:
            continue

        sequence = cells[0][0].strip()
        if not sequence:
            continue
        try:
            document_name = _document_name_from_cell(*cells[2])
        except ValueError:
            continue
        key = (sequence, document_name)
        if key in seen:
            continue
        seen.add(key)
        results.append(
            InventoryFile(
                sequence_number=sequence,
                description=cells[1][0].strip() or None,
                document_name=document_name,
                document_type=cells[3][0].strip() or None,
                declared_size_bytes=(
                    _parse_size(cells[4][0]) if len(cells) >= 5 else None
                ),
                table_section=section or "unknown",
            )
        )
        if len(results) > max_rows:
            raise SecArchiveError(
                f"Índice SEC excede el límite de {max_rows} archivos"
            )

    if not results or not any(
        "document" in item.table_section.casefold()
        for item in results
    ):
        raise SecArchiveError(
            "Índice SEC no contiene filas válidas de Document Format Files"
        )

    return results


def _fallback_primary_key(item: InventoryFile) -> tuple[int, int | str, str]:
    try:
        return (0, int(item.sequence_number), item.document_name.casefold())
    except ValueError:
        return (1, item.sequence_number, item.document_name.casefold())


def with_primary_flag(
    files: Iterable[InventoryFile],
    primary_document: str | None,
    *,
    primary_description: str | None,
    form: str,
) -> list[InventoryFile]:
    items = list(files)
    safe_primary = (
        safe_document_name(primary_document)
        if primary_document
        else None
    )
    target_key: tuple[str, str] | None = None

    if safe_primary is not None:
        matches = [
            item
            for item in items
            if item.document_name.casefold() == safe_primary.casefold()
        ]
        if matches:
            target = min(matches, key=_fallback_primary_key)
            target_key = (target.sequence_number, target.document_name)
    else:
        document_rows = [
            item
            for item in items
            if "document" in item.table_section.casefold()
        ]
        form_matches = [
            item
            for item in document_rows
            if (item.document_type or "").casefold() == form.casefold()
        ]
        candidates = form_matches or document_rows
        if candidates:
            target = min(candidates, key=_fallback_primary_key)
            target_key = (target.sequence_number, target.document_name)

    updated = [
        InventoryFile(
            sequence_number=item.sequence_number,
            document_name=item.document_name,
            document_type=item.document_type,
            description=item.description,
            declared_size_bytes=item.declared_size_bytes,
            table_section=item.table_section,
            is_primary=(
                target_key == (item.sequence_number, item.document_name)
            ),
        )
        for item in items
    ]

    if safe_primary is not None and target_key is None:
        updated.insert(
            0,
            InventoryFile(
                sequence_number="__metadata_primary__",
                document_name=safe_primary,
                document_type=form,
                description=primary_description,
                declared_size_bytes=None,
                table_section="submissions_metadata_fallback",
                is_primary=True,
            ),
        )
    return updated


def _json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, separators=(",", ":"))


def _copy_asset_links(
    conn: sqlite3.Connection,
    *,
    source_raw_document_id: str,
    target_raw_document_id: str,
) -> None:
    conn.execute(
        """
        INSERT OR IGNORE INTO raw_document_assets(
            raw_document_id,
            asset_id,
            role,
            linking_method,
            linking_version,
            confidence,
            metadata_json
        )
        SELECT
            ?,
            asset_id,
            role,
            'sec_filing_lineage',
            ?,
            confidence,
            '{"inherited_from_filing":true}'
        FROM raw_document_assets
        WHERE raw_document_id = ?
        """,
        (
            target_raw_document_id,
            DOCUMENT_VERSION,
            source_raw_document_id,
        ),
    )


def persist_raw_response(
    conn: sqlite3.Connection,
    store: ContentAddressedRawStore,
    *,
    filing: FilingRecord,
    response: SecResponse,
    external_id: str,
    document_kind: str,
    category: str,
    parent_raw_document_id: str,
) -> PersistedRaw:
    digest = hashlib.sha256(response.payload).hexdigest()
    existing_rows = conn.execute(
        """
        SELECT raw_document_id, raw_sha256
        FROM raw_source_documents
        WHERE source_id = ? AND external_id = ?
        ORDER BY created_at, raw_document_id
        """,
        (SOURCE_ID, external_id),
    ).fetchall()
    identical = next(
        (str(row[0]) for row in existing_rows if row[1] == digest),
        None,
    )
    revision_observed = bool(existing_rows) and identical is None
    available_at = (
        response.retrieved_at
        if revision_observed
        else filing.acceptance_datetime
    )

    stored = store.write(
        category=category,
        year=filing.acceptance_datetime[:4],
        cik=filing.cik,
        accession_number=filing.accession_number,
        payload=response.payload,
    )
    raw_document_id = hashlib.sha256(
        f"{SOURCE_ID}\0{external_id}\0{digest}".encode("utf-8")
    ).hexdigest()

    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO raw_source_documents(
            raw_document_id,
            source_id,
            external_id,
            document_kind,
            source_url,
            canonical_url,
            published_at,
            available_at,
            retrieved_at,
            modified_at,
            content_type,
            content_encoding,
            raw_sha256,
            storage_path,
            byte_length,
            parser_status,
            parent_raw_document_id,
            metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'gzip', ?, ?, ?, 'raw', ?, ?)
        """,
        (
            raw_document_id,
            SOURCE_ID,
            external_id,
            document_kind,
            response.requested_url,
            response.final_url,
            filing.acceptance_datetime,
            available_at,
            response.retrieved_at,
            response.last_modified,
            response.content_type,
            stored.sha256,
            str(stored.path),
            stored.byte_length,
            parent_raw_document_id,
            _json(
                {
                    "exact_response_entity_bytes": True,
                    "transport_content_encoding": response.transport_encoding,
                    "http_status": response.status,
                    "response_headers": response.headers,
                    "storage_encoding": "gzip",
                    "availability_source": (
                        "retrieved_at_revision"
                        if revision_observed
                        else "sec_acceptance_datetime"
                    ),
                    "importance_not_assigned": True,
                    "sentiment_not_assigned": True,
                }
            ),
        ),
    )
    persisted_id = identical or raw_document_id
    return PersistedRaw(
        raw_document_id=persisted_id,
        inserted=cursor.rowcount == 1,
        revision_observed=revision_observed,
        sha256=digest,
    )


def _upsert_inventory_file(
    conn: sqlite3.Connection,
    *,
    filing: FilingRecord,
    item: InventoryFile,
    inventory_raw_document_id: str,
    observed_at: str,
) -> None:
    source_url = archive_file_url(
        filing.cik,
        filing.accession_number,
        item.document_name,
    )
    metadata = _json(
        {
            "table_section": item.table_section,
            "inventory_parser_version": INVENTORY_PARSER_VERSION,
        }
    )
    conn.execute(
        """
        INSERT INTO sec_filing_files(
            filing_raw_document_id,
            sequence_number,
            document_name,
            document_type,
            description,
            source_url,
            is_primary,
            metadata_json,
            inventory_raw_document_id,
            declared_size_bytes,
            discovered_at,
            last_seen_at,
            download_status,
            inventory_status
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 'pending', 'current')
        ON CONFLICT(
            filing_raw_document_id,
            sequence_number,
            document_name
        ) DO UPDATE SET
            document_type = excluded.document_type,
            description = excluded.description,
            source_url = excluded.source_url,
            is_primary = excluded.is_primary,
            metadata_json = excluded.metadata_json,
            inventory_raw_document_id = excluded.inventory_raw_document_id,
            declared_size_bytes = excluded.declared_size_bytes,
            last_seen_at = excluded.last_seen_at,
            inventory_status = 'current'
        """,
        (
            filing.raw_document_id,
            item.sequence_number,
            item.document_name,
            item.document_type,
            item.description,
            source_url,
            int(item.is_primary),
            metadata,
            inventory_raw_document_id,
            item.declared_size_bytes,
            observed_at,
            observed_at,
        ),
    )


def _supersede_absent_inventory_rows(
    conn: sqlite3.Connection,
    *,
    filing: FilingRecord,
    current_inventory: Iterable[InventoryFile],
) -> None:
    current_keys = {
        (item.sequence_number, item.document_name)
        for item in current_inventory
    }
    existing = conn.execute(
        """
        SELECT sequence_number, document_name
        FROM sec_filing_files
        WHERE filing_raw_document_id = ?
        """,
        (filing.raw_document_id,),
    ).fetchall()
    for sequence_number, document_name in existing:
        key = (str(sequence_number), str(document_name))
        if key in current_keys:
            continue
        conn.execute(
            """
            UPDATE sec_filing_files
            SET inventory_status = 'superseded'
            WHERE filing_raw_document_id = ?
              AND sequence_number = ?
              AND document_name = ?
            """,
            (
                filing.raw_document_id,
                sequence_number,
                document_name,
            ),
        )


def _sequence_key(item: InventoryFile) -> tuple[int, int | str, str]:
    if item.is_primary:
        return (0, 0, item.document_name)
    try:
        sequence: int | str = int(item.sequence_number)
        numeric = 0
    except ValueError:
        sequence = item.sequence_number
        numeric = 1
    return (1 + numeric, sequence, item.document_name)


def selected_documents(
    files: Iterable[InventoryFile],
    *,
    max_files: int,
) -> tuple[list[tuple[InventoryFile, str]], list[InventoryFile]]:
    if max_files <= 0:
        raise ValueError("max_files debe ser mayor que cero")
    candidates: list[tuple[InventoryFile, str]] = []
    for item in sorted(files, key=_sequence_key):
        document_type = (item.document_type or "").upper()
        if item.is_primary:
            candidates.append((item, "primary"))
        elif document_type.startswith("EX-"):
            candidates.append((item, "exhibit"))

    selected = candidates[:max_files]
    skipped = [item for item, _ in candidates[max_files:]]
    return selected, skipped


def _update_file_status(
    conn: sqlite3.Connection,
    filing: FilingRecord,
    item: InventoryFile,
    *,
    status: str,
    attempted_at: str | None = None,
    downloaded_at: str | None = None,
    selection_reason: str | None = None,
    error: dict | None = None,
    preserve_download_success: bool = False,
    run_id: str | None = None,
) -> None:
    if preserve_download_success:
        existing = conn.execute(
            """
            SELECT raw_document_id
            FROM sec_filing_files
            WHERE filing_raw_document_id = ?
              AND sequence_number = ?
              AND document_name = ?
            """,
            (
                filing.raw_document_id,
                item.sequence_number,
                item.document_name,
            ),
        ).fetchone()
        if existing is not None and existing[0] is not None:
            if attempted_at is None:
                return
            conn.execute(
                """
                UPDATE sec_filing_files
                SET last_attempted_at = ?,
                    selection_reason = COALESCE(?, selection_reason),
                    error_json = ?,
                    last_attempt_run_id = COALESCE(
                        ?,
                        last_attempt_run_id
                    )
                WHERE filing_raw_document_id = ?
                  AND sequence_number = ?
                  AND document_name = ?
                """,
                (
                    attempted_at,
                    selection_reason,
                    _json(error) if error is not None else None,
                    run_id,
                    filing.raw_document_id,
                    item.sequence_number,
                    item.document_name,
                ),
            )
            return

    conn.execute(
        """
        UPDATE sec_filing_files
        SET download_status = ?,
            last_attempted_at = COALESCE(?, last_attempted_at),
            downloaded_at = COALESCE(?, downloaded_at),
            selection_reason = COALESCE(?, selection_reason),
            error_json = ?,
            last_attempt_run_id = COALESCE(?, last_attempt_run_id)
        WHERE filing_raw_document_id = ?
          AND sequence_number = ?
          AND document_name = ?
        """,
        (
            status,
            attempted_at,
            downloaded_at,
            selection_reason,
            _json(error) if error is not None else None,
            run_id,
            filing.raw_document_id,
            item.sequence_number,
            item.document_name,
        ),
    )


def _canonical_file_raw_id(
    conn: sqlite3.Connection,
    filing: FilingRecord,
    item: InventoryFile,
) -> str | None:
    row = conn.execute(
        """
        SELECT raw_document_id
        FROM sec_filing_files
        WHERE filing_raw_document_id = ?
          AND sequence_number = ?
          AND document_name = ?
        """,
        (
            filing.raw_document_id,
            item.sequence_number,
            item.document_name,
        ),
    ).fetchone()
    return str(row[0]) if row and row[0] else None


def _observation_id(kind: str, *parts: str) -> str:
    material = "\0".join((kind, *parts)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _link_file_version(
    conn: sqlite3.Connection,
    *,
    filing: FilingRecord,
    item: InventoryFile,
    persisted: PersistedRaw,
    observed_at: str,
    run_id: str,
) -> str:
    canonical_raw_document_id = _canonical_file_raw_id(conn, filing, item)
    canonical_assigned = canonical_raw_document_id is None
    if canonical_assigned:
        canonical_raw_document_id = persisted.raw_document_id
        conn.execute(
            """
            UPDATE sec_filing_files
            SET raw_document_id = ?
            WHERE filing_raw_document_id = ?
              AND sequence_number = ?
              AND document_name = ?
            """,
            (
                persisted.raw_document_id,
                filing.raw_document_id,
                item.sequence_number,
                item.document_name,
            ),
        )

    is_canonical_content = (
        canonical_raw_document_id == persisted.raw_document_id
    )
    content_status = (
        "canonical" if is_canonical_content else "revision_observed"
    )
    version_cursor = conn.execute(
        """
        INSERT OR IGNORE INTO sec_filing_file_versions(
            filing_raw_document_id,
            sequence_number,
            document_name,
            raw_document_id,
            observed_at,
            retrieval_run_id,
            version_status,
            metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            filing.raw_document_id,
            item.sequence_number,
            item.document_name,
            persisted.raw_document_id,
            observed_at,
            run_id,
            content_status,
            _json(
                {
                    "canonical_pointer_changed": canonical_assigned,
                    "revision_not_promoted_causally": not is_canonical_content,
                }
            ),
        ),
    )
    content_first_seen = version_cursor.rowcount == 1
    if is_canonical_content:
        observation_status = (
            "canonical_first_seen"
            if content_first_seen
            else "canonical_rerun"
        )
    else:
        observation_status = (
            "revision_first_seen"
            if content_first_seen
            else "revision_rerun"
        )

    observation_id = _observation_id(
        "sec_filing_file",
        filing.raw_document_id,
        item.sequence_number,
        item.document_name,
        run_id,
        persisted.raw_document_id,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO sec_filing_file_observations(
            observation_id,
            filing_raw_document_id,
            sequence_number,
            document_name,
            raw_document_id,
            observed_at,
            retrieval_run_id,
            observation_status,
            metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            observation_id,
            filing.raw_document_id,
            item.sequence_number,
            item.document_name,
            persisted.raw_document_id,
            observed_at,
            run_id,
            observation_status,
            _json(
                {
                    "canonical_raw_document_id": canonical_raw_document_id,
                    "content_first_seen": content_first_seen,
                    "raw_content_inserted": persisted.inserted,
                }
            ),
        ),
    )
    return observation_status


def _record_inventory_observation(
    conn: sqlite3.Connection,
    *,
    filing: FilingRecord,
    persisted_index: PersistedRaw,
    observed_at: str,
    run_id: str,
    file_count: int,
    source_url: str,
) -> None:
    observation_id = _observation_id(
        "sec_filing_inventory",
        filing.raw_document_id,
        run_id,
        persisted_index.raw_document_id,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO sec_filing_inventory_observations(
            observation_id,
            filing_raw_document_id,
            inventory_raw_document_id,
            observed_at,
            parser_version,
            file_count,
            retrieval_run_id,
            metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            observation_id,
            filing.raw_document_id,
            persisted_index.raw_document_id,
            observed_at,
            INVENTORY_PARSER_VERSION,
            file_count,
            run_id,
            _json({"source_url": source_url}),
        ),
    )


def process_filing(
    conn: sqlite3.Connection,
    store: ContentAddressedRawStore,
    client: SecByteClient,
    filing: FilingRecord,
    *,
    run_id: str,
    budget: ByteBudget,
    max_files: int,
    max_file_bytes: int,
    max_index_bytes: int,
) -> DownloadStats:
    if conn.in_transaction:
        raise sqlite3.ProgrammingError(
            "process_filing requiere una conexión sin transacción activa "
            "antes de iniciar las descargas SEC"
        )

    stats = DownloadStats()
    index_url = archive_index_url(filing.cik, filing.accession_number)
    index_response = client.get_bytes(
        index_url,
        max_bytes=min(max_index_bytes, budget.remaining),
    )
    budget.consume(len(index_response.payload))
    inventory = with_primary_flag(
        parse_filing_index(index_response.payload),
        filing.primary_document,
        primary_description=filing.primary_doc_description,
        form=filing.form,
    )
    metadata_primary_fallback = any(
        item.table_section == "submissions_metadata_fallback"
        for item in inventory
    )
    selected, skipped_limit = selected_documents(
        inventory,
        max_files=max_files,
    )
    eligible_keys = {
        (item.sequence_number, item.document_name)
        for item, _ in selected
    } | {
        (item.sequence_number, item.document_name)
        for item in skipped_limit
    }

    deferred_statuses: list[
        tuple[InventoryFile, str, str, dict | None]
    ] = []
    for item in inventory:
        key = (item.sequence_number, item.document_name)
        if key in eligible_keys:
            continue
        stats.files_skipped += 1
        deferred_statuses.append(
            (
                item,
                "skipped_policy",
                "not_primary_or_exhibit",
                None,
            )
        )
    for item in skipped_limit:
        stats.files_skipped += 1
        deferred_statuses.append(
            (
                item,
                "skipped_limit",
                "exceeds_max_files_per_filing",
                None,
            )
        )

    stats.documents_discovered = len(inventory)
    fetched_files: list[FetchedFile] = []
    for item, reason in selected:
        declared = item.declared_size_bytes
        if declared is not None and declared > max_file_bytes:
            stats.files_skipped += 1
            deferred_statuses.append(
                (
                    item,
                    "skipped_size",
                    reason,
                    {
                        "declared_size_bytes": declared,
                        "max_file_bytes": max_file_bytes,
                    },
                )
            )
            continue
        if budget.remaining <= 0 or (
            declared is not None and declared > budget.remaining
        ):
            stats.files_skipped += 1
            deferred_statuses.append(
                (
                    item,
                    "skipped_budget",
                    reason,
                    {"remaining_bytes": budget.remaining},
                )
            )
            continue

        if conn.in_transaction:
            raise sqlite3.ProgrammingError(
                "Se abrió una transacción SQLite durante la fase de fetch SEC"
            )
        attempted_at = utc_now()
        file_url = archive_file_url(
            filing.cik,
            filing.accession_number,
            item.document_name,
        )
        try:
            response = client.get_bytes(
                file_url,
                max_bytes=min(max_file_bytes, budget.remaining),
            )
            budget.consume(len(response.payload))
        except EXPECTED_OPERATIONAL_ERRORS as error:
            error_message = str(error)
            stats.errors.append(
                {
                    "accession_number": filing.accession_number,
                    "document_name": item.document_name,
                    "error": error_message,
                }
            )
            fetched_files.append(
                FetchedFile(
                    item=item,
                    selection_reason=reason,
                    attempted_at=attempted_at,
                    error_message=error_message,
                )
            )
        else:
            fetched_files.append(
                FetchedFile(
                    item=item,
                    selection_reason=reason,
                    attempted_at=attempted_at,
                    response=response,
                )
            )

    if conn.in_transaction:
        raise sqlite3.ProgrammingError(
            "La fase de fetch SEC dejó una transacción SQLite activa"
        )

    index_external_id = (
        f"filings/{filing.cik}/{filing.accession_number}/index.html"
    )
    conn.execute("BEGIN")
    try:
        persisted_index = persist_raw_response(
            conn,
            store,
            filing=filing,
            response=index_response,
            external_id=index_external_id,
            document_kind="sec_filing_index_html",
            category="indexes",
            parent_raw_document_id=filing.raw_document_id,
        )
        stats.indexes_inserted += int(persisted_index.inserted)
        _copy_asset_links(
            conn,
            source_raw_document_id=filing.raw_document_id,
            target_raw_document_id=persisted_index.raw_document_id,
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO sec_filing_inventory_snapshots(
                filing_raw_document_id,
                inventory_raw_document_id,
                observed_at,
                parser_version,
                file_count,
                retrieval_run_id,
                metadata_json
            )
            VALUES (?, ?, ?, ?, ?, ?, ?)
            """,
            (
                filing.raw_document_id,
                persisted_index.raw_document_id,
                index_response.retrieved_at,
                INVENTORY_PARSER_VERSION,
                len(inventory),
                run_id,
                _json({"source_url": index_response.requested_url}),
            ),
        )
        _record_inventory_observation(
            conn,
            filing=filing,
            persisted_index=persisted_index,
            observed_at=index_response.retrieved_at,
            run_id=run_id,
            file_count=len(inventory),
            source_url=index_response.requested_url,
        )

        for item in inventory:
            _upsert_inventory_file(
                conn,
                filing=filing,
                item=item,
                inventory_raw_document_id=persisted_index.raw_document_id,
                observed_at=index_response.retrieved_at,
            )
        if not metadata_primary_fallback:
            _supersede_absent_inventory_rows(
                conn,
                filing=filing,
                current_inventory=inventory,
            )

        for item, status, reason, error in deferred_statuses:
            _update_file_status(
                conn,
                filing,
                item,
                status=status,
                selection_reason=reason,
                error=error,
                preserve_download_success=True,
            )

        for fetched in fetched_files:
            item = fetched.item
            if fetched.error_message is not None:
                _update_file_status(
                    conn,
                    filing,
                    item,
                    status="failed",
                    attempted_at=fetched.attempted_at,
                    selection_reason=fetched.selection_reason,
                    error={"error": fetched.error_message},
                    preserve_download_success=True,
                    run_id=run_id,
                )
                continue

            response = fetched.response
            assert response is not None
            external_id = (
                f"filings/{filing.cik}/{filing.accession_number}/"
                f"files/{item.document_name}"
            )
            try:
                persisted = persist_raw_response(
                    conn,
                    store,
                    filing=filing,
                    response=response,
                    external_id=external_id,
                    document_kind=(
                        "sec_filing_primary_document"
                        if item.is_primary
                        else "sec_filing_exhibit"
                    ),
                    category="documents",
                    parent_raw_document_id=persisted_index.raw_document_id,
                )
                version_status = _link_file_version(
                    conn,
                    filing=filing,
                    item=item,
                    persisted=persisted,
                    observed_at=response.retrieved_at,
                    run_id=run_id,
                )
                _copy_asset_links(
                    conn,
                    source_raw_document_id=filing.raw_document_id,
                    target_raw_document_id=persisted.raw_document_id,
                )
            except EXPECTED_OPERATIONAL_ERRORS as error:
                error_message = str(error)
                stats.errors.append(
                    {
                        "accession_number": filing.accession_number,
                        "document_name": item.document_name,
                        "error": error_message,
                    }
                )
                _update_file_status(
                    conn,
                    filing,
                    item,
                    status="failed",
                    attempted_at=fetched.attempted_at,
                    selection_reason=fetched.selection_reason,
                    error={"error": error_message},
                    preserve_download_success=True,
                    run_id=run_id,
                )
                continue

            stats.files_downloaded += 1
            if persisted.inserted:
                stats.documents_inserted += 1
            else:
                stats.documents_existing += 1
            if version_status == "revision_first_seen":
                stats.revisions_observed += 1
            if version_status.startswith("revision_"):
                status = "content_changed"
                error = {
                    "reason": "official_url_returned_different_bytes",
                    "revision_not_promoted_causally": True,
                }
            else:
                status = "downloaded"
                error = None
            _update_file_status(
                conn,
                filing,
                item,
                status=status,
                attempted_at=fetched.attempted_at,
                downloaded_at=response.retrieved_at,
                selection_reason=fetched.selection_reason,
                error=error,
                run_id=run_id,
            )
    except BaseException:
        conn.rollback()
        raise
    else:
        conn.commit()

    return stats


def ensure_contract(conn: sqlite3.Connection) -> None:
    required = {
        "ingestion_sources",
        "source_ingestion_runs",
        "raw_source_documents",
        "raw_document_assets",
        "sec_filings",
        "sec_filing_files",
        "sec_filing_inventory_snapshots",
        "sec_filing_inventory_observations",
        "sec_filing_file_versions",
        "sec_filing_file_observations",
    }
    existing = {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }
    missing = sorted(required - existing)
    if missing:
        raise RuntimeError(
            "Aplicá migrations 011, 012 y 014 antes de descargar documentos SEC. "
            f"Faltan: {missing}"
        )
    columns = {
        str(row[1])
        for row in conn.execute("PRAGMA table_info(sec_filing_files)")
    }
    required_columns = {
        "inventory_raw_document_id",
        "declared_size_bytes",
        "download_status",
        "last_attempted_at",
        "last_attempt_run_id",
        "downloaded_at",
        "inventory_status",
    }
    missing_columns = sorted(required_columns - columns)
    if missing_columns:
        raise RuntimeError(
            "Migraciones SEC 012/014 incompletas. Faltan columnas: "
            f"{missing_columns}"
        )


def select_filings(
    conn: sqlite3.Connection,
    *,
    accessions: list[str],
    max_filings: int,
) -> list[FilingRecord]:
    parameters: list[object] = []
    where = ""
    if accessions:
        normalized = [validate_accession(value) for value in accessions]
        placeholders = ",".join("?" for _ in normalized)
        where = f"WHERE accession_number IN ({placeholders})"
        parameters.extend(normalized)
    parameters.append(max_filings)
    rows = conn.execute(
        f"""
        SELECT
            raw_document_id,
            cik,
            accession_number,
            form,
            acceptance_datetime,
            primary_document,
            primary_doc_description
        FROM sec_filings
        {where}
        ORDER BY acceptance_datetime DESC, accession_number
        LIMIT ?
        """,
        parameters,
    ).fetchall()
    return [
        FilingRecord(
            raw_document_id=str(row[0]),
            cik=str(row[1]),
            accession_number=str(row[2]),
            form=str(row[3]),
            acceptance_datetime=str(row[4]),
            primary_document=str(row[5]) if row[5] else None,
            primary_doc_description=str(row[6]) if row[6] else None,
        )
        for row in rows
    ]


def execute_ingestion_run(
    conn: sqlite3.Connection,
    store: ContentAddressedRawStore,
    client: SecByteClient,
    *,
    accessions: list[str],
    max_filings: int,
    max_files_per_filing: int,
    max_file_bytes: int,
    max_index_bytes: int,
    max_total_bytes: int,
    run_id: str | None = None,
    started_at: str | None = None,
    select_fn: Callable[..., list[FilingRecord]] = select_filings,
) -> dict:
    ensure_contract(conn)
    actual_run_id = run_id or uuid.uuid4().hex
    actual_started_at = started_at or utc_now()
    budget = ByteBudget(max_total_bytes)
    totals = DownloadStats()
    filings: list[FilingRecord] = []
    status = "failed"
    fatal_error: BaseException | None = None

    conn.execute(
        """
        INSERT INTO source_ingestion_runs(
            run_id,
            source_id,
            mode,
            started_at,
            status
        )
        VALUES (?, ?, 'filing_documents', ?, 'running')
        """,
        (actual_run_id, SOURCE_ID, actual_started_at),
    )
    conn.commit()

    try:
        filings = select_fn(
            conn,
            accessions=accessions,
            max_filings=max_filings,
        )
        for filing in filings:
            try:
                stats = process_filing(
                    conn,
                    store,
                    client,
                    filing,
                    run_id=actual_run_id,
                    budget=budget,
                    max_files=max_files_per_filing,
                    max_file_bytes=max_file_bytes,
                    max_index_bytes=max_index_bytes,
                )
            except EXPECTED_OPERATIONAL_ERRORS as error:
                conn.rollback()
                totals.errors.append(
                    {
                        "accession_number": filing.accession_number,
                        "error": str(error),
                    }
                )
            else:
                totals.merge(stats)
        status = (
            "completed"
            if not totals.errors
            else "completed_with_errors"
        )
    except BaseException as error:
        conn.rollback()
        fatal_error = error
        totals.errors.append(
            {
                "stage": "run",
                "error": str(error),
            }
        )
        status = "failed"
    finally:
        conn.execute(
            """
            UPDATE source_ingestion_runs
            SET finished_at = ?,
                status = ?,
                documents_discovered = ?,
                documents_inserted = ?,
                documents_existing = ?,
                error_count = ?,
                error_json = ?
            WHERE run_id = ?
            """,
            (
                utc_now(),
                status,
                totals.documents_discovered,
                totals.documents_inserted,
                totals.documents_existing,
                len(totals.errors),
                _json(
                    {
                        "errors": totals.errors,
                        "indexes_inserted": totals.indexes_inserted,
                        "files_downloaded": totals.files_downloaded,
                        "files_skipped": totals.files_skipped,
                        "revisions_observed": totals.revisions_observed,
                        "bytes_downloaded": budget.used,
                    }
                ),
                actual_run_id,
            ),
        )
        conn.commit()

    result = {
        "run_id": actual_run_id,
        "status": status,
        "filings_selected": len(filings),
        "documents_discovered": totals.documents_discovered,
        "documents_inserted": totals.documents_inserted,
        "documents_existing": totals.documents_existing,
        "indexes_inserted": totals.indexes_inserted,
        "files_downloaded": totals.files_downloaded,
        "files_skipped": totals.files_skipped,
        "revisions_observed": totals.revisions_observed,
        "bytes_downloaded": budget.used,
        "errors": totals.errors,
    }
    if fatal_error is not None:
        try:
            setattr(fatal_error, "sec_ingestion_result", result)
        except Exception:
            pass
        raise fatal_error

    return result


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--accession", action="append", default=[])
    parser.add_argument("--max-filings", type=int, default=DEFAULT_MAX_FILINGS)
    parser.add_argument(
        "--max-files-per-filing",
        type=int,
        default=DEFAULT_MAX_FILES_PER_FILING,
    )
    parser.add_argument(
        "--max-file-bytes",
        type=int,
        default=DEFAULT_MAX_FILE_BYTES,
    )
    parser.add_argument(
        "--max-index-bytes",
        type=int,
        default=DEFAULT_MAX_INDEX_BYTES,
    )
    parser.add_argument(
        "--max-total-bytes",
        type=int,
        default=DEFAULT_MAX_TOTAL_BYTES,
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument(
        "--max-retry-after-seconds",
        type=float,
        default=DEFAULT_MAX_RETRY_AFTER_SECONDS,
    )
    parser.add_argument("--rate-limit", type=float, default=DEFAULT_RATE_LIMIT)
    parser.add_argument("--user-agent")
    args = parser.parse_args()

    positive_limits = {
        "--max-filings": args.max_filings,
        "--max-files-per-filing": args.max_files_per_filing,
        "--max-file-bytes": args.max_file_bytes,
        "--max-index-bytes": args.max_index_bytes,
        "--max-retry-after-seconds": args.max_retry_after_seconds,
        "--max-total-bytes": args.max_total_bytes,
    }
    invalid = [name for name, value in positive_limits.items() if value <= 0]
    if invalid:
        raise SystemExit(f"Los límites deben ser positivos: {invalid}")

    user_agent = args.user_agent or os.environ.get("SEC_USER_AGENT")
    client = SecArchiveClient(
        validate_user_agent(user_agent),
        max_retry_after_seconds=args.max_retry_after_seconds,
        rate_limit_per_second=args.rate_limit,
    )
    store = ContentAddressedRawStore(args.raw_root)
    cli_run_id = uuid.uuid4().hex
    try:
        with sqlite3.connect(args.db) as conn:
            conn.execute("PRAGMA foreign_keys = ON")
            result = execute_ingestion_run(
                conn,
                store,
                client,
                accessions=args.accession,
                max_filings=args.max_filings,
                max_files_per_filing=args.max_files_per_filing,
                max_file_bytes=args.max_file_bytes,
                max_index_bytes=args.max_index_bytes,
                max_total_bytes=args.max_total_bytes,
                run_id=cli_run_id,
            )
    except Exception as error:
        result = getattr(
            error,
            "sec_ingestion_result",
            {
                "run_id": cli_run_id,
                "status": "failed",
                "filings_selected": 0,
                "documents_discovered": 0,
                "documents_inserted": 0,
                "documents_existing": 0,
                "indexes_inserted": 0,
                "files_downloaded": 0,
                "files_skipped": 0,
                "revisions_observed": 0,
                "bytes_downloaded": 0,
                "errors": [{"stage": "cli", "error": str(error)}],
            },
        )

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

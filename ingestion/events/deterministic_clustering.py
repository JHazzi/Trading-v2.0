from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import re
import sqlite3
import unicodedata
import uuid
from dataclasses import asdict, dataclass
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Iterable, Sequence


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
ALGORITHM_NAME = "causal_blocked_document_clustering"
TOKEN_RE = re.compile(r"[^\W_]+(?:['’-][^\W_]+)?", re.UNICODE)
TEXT_CONTENT_TYPES = {
    "application/json",
    "application/xhtml+xml",
    "application/xml",
    "text/html",
    "text/plain",
    "text/xml",
}


def _canonical_json(value: object) -> str:
    return json.dumps(
        value,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


def _sha256_text(value: str) -> str:
    return hashlib.sha256(value.encode("utf-8")).hexdigest()


def _utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _normalized_timestamp(value: str, *, field: str) -> str:
    candidate = str(value).strip()
    if not candidate:
        raise ValueError(f"{field} no puede estar vacío")
    if candidate.endswith("Z"):
        candidate = candidate[:-1] + "+00:00"
    try:
        parsed = datetime.fromisoformat(candidate)
    except ValueError as error:
        raise ValueError(f"{field} no es ISO-8601: {value!r}") from error
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def _timestamp_value(value: str) -> datetime:
    return datetime.fromisoformat(value)


@dataclass(frozen=True)
class ClusteringConfig:
    cluster_version: str = "deterministic_event_cluster_v1"
    fingerprint_version: str = "document_fingerprint_v1"
    normalization_version: str = "unicode_nfkc_tokens_v1"
    near_duplicate_threshold: float = 0.82
    shingle_size: int = 3
    simhash_bands: int = 4
    min_near_duplicate_tokens: int = 8
    min_exact_duplicate_tokens: int = 8
    min_length_ratio: float = 0.72
    near_duplicate_max_age_seconds: int = 259_200
    exact_duplicate_max_age_seconds: int = 604_800
    max_candidates_per_document: int = 128
    require_asset_overlap_for_exact_duplicate: bool = True
    require_asset_overlap_for_near_duplicate: bool = True
    max_fingerprint_tokens: int = 50_000
    max_content_bytes: int = 10 * 1024 * 1024

    def __post_init__(self) -> None:
        if not self.cluster_version or not self.fingerprint_version:
            raise ValueError("Las versiones de clustering/fingerprint son obligatorias")
        if not 0.0 <= self.near_duplicate_threshold <= 1.0:
            raise ValueError("near_duplicate_threshold debe estar entre 0 y 1")
        if self.shingle_size < 1:
            raise ValueError("shingle_size debe ser positivo")
        if self.simhash_bands < 1 or 64 % self.simhash_bands:
            raise ValueError("simhash_bands debe dividir exactamente 64")
        if self.min_near_duplicate_tokens < self.shingle_size:
            raise ValueError(
                "min_near_duplicate_tokens no puede ser menor que shingle_size"
            )
        if self.min_exact_duplicate_tokens < 1:
            raise ValueError("min_exact_duplicate_tokens debe ser positivo")
        if not 0.0 <= self.min_length_ratio <= 1.0:
            raise ValueError("min_length_ratio debe estar entre 0 y 1")
        positive = {
            "near_duplicate_max_age_seconds": self.near_duplicate_max_age_seconds,
            "exact_duplicate_max_age_seconds": self.exact_duplicate_max_age_seconds,
            "min_exact_duplicate_tokens": self.min_exact_duplicate_tokens,
            "max_candidates_per_document": self.max_candidates_per_document,
            "max_fingerprint_tokens": self.max_fingerprint_tokens,
            "max_content_bytes": self.max_content_bytes,
        }
        invalid = [name for name, value in positive.items() if value <= 0]
        if invalid:
            raise ValueError(f"Los límites deben ser positivos: {invalid}")

    def payload(self) -> dict:
        return {
            "algorithm_name": ALGORITHM_NAME,
            **asdict(self),
        }

    def payload_json(self) -> str:
        return _canonical_json(self.payload())

    def sha256(self) -> str:
        return _sha256_text(self.payload_json())


@dataclass(frozen=True)
class SecObservation:
    observation_id: str
    observed_at: str


@dataclass(frozen=True)
class EvidenceDocument:
    evidence_type: str
    evidence_id: str
    available_at: str
    availability_basis: str
    availability_is_point_in_time: bool
    title: str | None
    text: str
    content_sha256: str
    source_name: str | None
    source_id: str | None
    asset_ids: tuple[int, ...]
    sec_accession_number: str | None = None
    raw_document_id: str | None = None
    content_type: str | None = None
    extraction_status: str = "available"
    sec_observations: tuple[SecObservation, ...] = ()

    @property
    def evidence_key(self) -> str:
        prefix = "news" if self.evidence_type == "news_document" else "raw"
        return f"{prefix}:{self.evidence_id}"


@dataclass(frozen=True)
class DocumentFingerprint:
    fingerprint_id: str
    normalized_text_sha256: str | None
    content_sha256: str
    simhash64_hex: str | None
    shingle_hashes: tuple[str, ...]
    blocking_keys: tuple[str, ...]
    token_count: int
    text_length: int


@dataclass(frozen=True)
class Assignment:
    evidence_type: str
    evidence_id: str
    evidence_key: str
    cluster_id: str
    match_method: str
    available_at: str
    matched_evidence_key: str | None
    matched_membership_id: str | None
    similarity: float | None
    decision_order: int
    membership_id: str


@dataclass(frozen=True)
class _AssignedRecord:
    document: EvidenceDocument
    fingerprint: DocumentFingerprint
    assignment: Assignment


class _VisibleTextParser(HTMLParser):
    def __init__(self) -> None:
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self._ignored_depth = 0

    def handle_starttag(
        self,
        tag: str,
        attrs: list[tuple[str, str | None]],
    ) -> None:
        del attrs
        if tag.lower() in {"script", "style", "noscript"}:
            self._ignored_depth += 1

    def handle_endtag(self, tag: str) -> None:
        if tag.lower() in {"script", "style", "noscript"}:
            self._ignored_depth = max(0, self._ignored_depth - 1)

    def handle_data(self, data: str) -> None:
        if not self._ignored_depth:
            self.parts.append(data)


def _visible_text(value: str) -> str:
    if "<" not in value or ">" not in value:
        return html.unescape(value)
    parser = _VisibleTextParser()
    try:
        parser.feed(value)
        parser.close()
    except (ValueError, AssertionError):
        return html.unescape(value)
    return " ".join(parser.parts)


def normalize_text(value: str) -> tuple[str, tuple[str, ...]]:
    visible = _visible_text(value)
    normalized = unicodedata.normalize("NFKC", visible).casefold()
    tokens = tuple(TOKEN_RE.findall(normalized))
    return " ".join(tokens), tokens


def _shingle_hashes(
    tokens: Sequence[str],
    *,
    size: int,
) -> tuple[str, ...]:
    if not tokens:
        return ()
    if len(tokens) < size:
        values: Iterable[str] = (" ".join(tokens),)
    else:
        values = (
            " ".join(tokens[index : index + size])
            for index in range(len(tokens) - size + 1)
        )
    return tuple(
        sorted(
            {
                hashlib.sha256(value.encode("utf-8")).hexdigest()[:16]
                for value in values
            }
        )
    )


def _simhash64(shingles: Sequence[str]) -> int | None:
    if not shingles:
        return None
    weights = [0] * 64
    for shingle in shingles:
        value = int(shingle, 16)
        for bit in range(64):
            weights[bit] += 1 if value & (1 << bit) else -1
    result = 0
    for bit, weight in enumerate(weights):
        if weight >= 0:
            result |= 1 << bit
    return result


def _blocking_keys(simhash: int | None, *, bands: int) -> tuple[str, ...]:
    if simhash is None:
        return ()
    width = 64 // bands
    mask = (1 << width) - 1
    digits = (width + 3) // 4
    return tuple(
        f"simhash:{band}:{(simhash >> (band * width)) & mask:0{digits}x}"
        for band in range(bands)
    )


def fingerprint_document(
    document: EvidenceDocument,
    config: ClusteringConfig,
) -> DocumentFingerprint:
    normalized, all_tokens = normalize_text(document.text)
    normalized_sha256 = _sha256_text(normalized) if normalized else None
    limited_tokens = all_tokens[: config.max_fingerprint_tokens]
    shingles = _shingle_hashes(limited_tokens, size=config.shingle_size)
    simhash = _simhash64(shingles)
    blocks = _blocking_keys(simhash, bands=config.simhash_bands)
    fingerprint_material = _canonical_json(
        {
            "evidence_type": document.evidence_type,
            "evidence_id": document.evidence_id,
            "fingerprint_version": config.fingerprint_version,
            "content_sha256": document.content_sha256,
            "normalized_text_sha256": normalized_sha256,
            "simhash64_hex": f"{simhash:016x}" if simhash is not None else None,
            "shingle_hashes": shingles,
        }
    )
    return DocumentFingerprint(
        fingerprint_id=f"fp_{_sha256_text(fingerprint_material)}",
        normalized_text_sha256=normalized_sha256,
        content_sha256=document.content_sha256,
        simhash64_hex=f"{simhash:016x}" if simhash is not None else None,
        shingle_hashes=shingles,
        blocking_keys=blocks,
        token_count=len(all_tokens),
        text_length=len(normalized),
    )


def _resolve_storage_path(storage_path: str, db: Path) -> Path:
    candidate = Path(storage_path)
    if candidate.is_absolute() and candidate.exists():
        return candidate
    possibilities = (
        ROOT / candidate,
        db.parent / candidate,
        candidate,
    )
    for possibility in possibilities:
        if possibility.exists():
            return possibility
    raise FileNotFoundError(f"No existe raw storage_path: {storage_path}")


def _read_raw_text(
    *,
    db: Path,
    storage_path: str,
    content_encoding: str | None,
    content_type: str | None,
    expected_sha256: str,
    expected_length: int,
    max_content_bytes: int,
) -> tuple[str, str]:
    if expected_length > max_content_bytes:
        return "", "skipped_size_limit"
    path = _resolve_storage_path(storage_path, db)
    opener = (
        gzip.open
        if (content_encoding or "").casefold() == "gzip"
        or path.suffix.casefold() == ".gz"
        else open
    )
    with opener(path, "rb") as stream:
        payload = stream.read(max_content_bytes + 1)
    if len(payload) > max_content_bytes:
        return "", "skipped_size_limit"
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if len(payload) != expected_length or actual_sha256 != expected_sha256:
        raise RuntimeError(
            "Raw document no coincide con byte_length/raw_sha256: "
            f"{path}"
        )

    media_type = (content_type or "").split(";", 1)[0].strip().casefold()
    looks_textual = (
        media_type in TEXT_CONTENT_TYPES
        or media_type.startswith("text/")
        or payload.lstrip().startswith((b"<", b"{", b"["))
    )
    if not looks_textual:
        return "", "skipped_non_text"
    return payload.decode("utf-8", errors="replace"), "available"


def _resolve_asset_filter(
    conn: sqlite3.Connection,
    *,
    asset_id: int | None,
    ticker: str | None,
) -> int | None:
    ticker_asset_id: int | None = None
    if ticker:
        row = conn.execute(
            "SELECT asset_id FROM assets WHERE upper(ticker) = upper(?)",
            (ticker.strip(),),
        ).fetchone()
        if row is None:
            raise ValueError(f"Ticker no existe en assets: {ticker!r}")
        ticker_asset_id = int(row[0])
    if asset_id is not None:
        exists = conn.execute(
            "SELECT 1 FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
        if exists is None:
            raise ValueError(f"asset_id no existe: {asset_id}")
    if (
        asset_id is not None
        and ticker_asset_id is not None
        and asset_id != ticker_asset_id
    ):
        raise ValueError("--asset-id y --ticker identifican activos distintos")
    return asset_id if asset_id is not None else ticker_asset_id


def _asset_ids_for_news(
    conn: sqlite3.Connection,
    news_id: str,
) -> tuple[int, ...]:
    return tuple(
        int(row[0])
        for row in conn.execute(
            """
            SELECT asset_id
            FROM news_assets
            WHERE news_id = ?
            ORDER BY asset_id
            """,
            (news_id,),
        )
    )


def _asset_ids_for_raw(
    conn: sqlite3.Connection,
    raw_document_id: str,
    filing_raw_document_id: str,
) -> tuple[int, ...]:
    return tuple(
        int(row[0])
        for row in conn.execute(
            """
            SELECT DISTINCT asset_id
            FROM raw_document_assets
            WHERE raw_document_id IN (?, ?)
            ORDER BY asset_id
            """,
            (raw_document_id, filing_raw_document_id),
        )
    )


@dataclass(frozen=True)
class _DocumentDescriptor:
    evidence_type: str
    evidence_id: str
    available_at: str
    availability_basis: str
    availability_is_point_in_time: bool
    asset_ids: tuple[int, ...]
    raw_document_id: str | None = None
    filing_raw_document_id: str | None = None
    sec_accession_number: str | None = None
    acceptance_at: str | None = None
    raw_sha256: str | None = None
    storage_path: str | None = None
    byte_length: int | None = None
    content_encoding: str | None = None
    content_type: str | None = None
    source_id: str | None = None
    title: str | None = None


def _asset_tuple(value: object) -> tuple[int, ...]:
    if value is None or str(value).strip() == "":
        return ()
    return tuple(
        sorted(
            {
                int(item)
                for item in str(value).split(",")
                if item.strip()
            }
        )
    )


def _news_descriptors(
    conn: sqlite3.Connection,
    *,
    asset_id: int | None,
) -> list[_DocumentDescriptor]:
    where = [
        "COALESCE(NULLIF(n.published_at, ''), n.ingested_at) IS NOT NULL"
    ]
    parameters: list[object] = []
    if asset_id is not None:
        where.append(
            """
            EXISTS (
                SELECT 1
                FROM news_assets AS selected_asset
                WHERE selected_asset.news_id = n.news_id
                  AND selected_asset.asset_id = ?
            )
            """
        )
        parameters.append(asset_id)
    rows = conn.execute(
        f"""
        SELECT
            n.news_id,
            n.published_at,
            n.ingested_at,
            group_concat(na.asset_id)
        FROM news_documents AS n
        LEFT JOIN news_assets AS na
          ON na.news_id = n.news_id
        WHERE {' AND '.join(where)}
        GROUP BY n.news_id, n.published_at, n.ingested_at
        """,
        parameters,
    ).fetchall()
    descriptors: list[_DocumentDescriptor] = []
    for row in rows:
        news_id = str(row[0])
        published_at = str(row[1]).strip() if row[1] else None
        available_at = _normalized_timestamp(
            published_at or str(row[2]),
            field=f"news_documents[{news_id}].available_at",
        )
        descriptors.append(
            _DocumentDescriptor(
                evidence_type="news_document",
                evidence_id=news_id,
                available_at=available_at,
                availability_basis=(
                    "legacy_published_at_assumed_not_pit_verified"
                    if published_at
                    else "legacy_ingested_at_assumed_not_pit_verified"
                ),
                availability_is_point_in_time=False,
                asset_ids=_asset_tuple(row[3]),
            )
        )
    return descriptors


def _table_exists(
    conn: sqlite3.Connection,
    table_name: str,
) -> bool:
    return conn.execute(
        """
        SELECT 1
        FROM sqlite_master
        WHERE type = 'table'
          AND name = ?
        """,
        (table_name,),
    ).fetchone() is not None

def _sec_descriptors(
    conn: sqlite3.Connection,
    *,
    asset_id: int | None,
) -> list[_DocumentDescriptor]:
    where = ["r.source_id = 'sec_edgar'"]
    parameters: list[object] = []
    if asset_id is not None:
        where.append(
            """
            EXISTS (
                SELECT 1
                FROM raw_document_assets AS selected_asset
                WHERE selected_asset.raw_document_id IN (
                    r.raw_document_id,
                    v.filing_raw_document_id
                )
                  AND selected_asset.asset_id = ?
            )
            """
        )
        parameters.append(asset_id)

    has_metadata_observations = _table_exists(
        conn,
        "sec_filing_metadata_observations",
    )	

    if has_metadata_observations:
        acceptance_pit_expression = """
            COALESCE((
                SELECT mo.availability_is_point_in_time
                FROM sec_filing_metadata_observations AS mo
                WHERE mo.filing_raw_document_id = v.filing_raw_document_id
                    AND julianday(mo.available_at) =
                    julianday(sf.acceptance_datetime)
                ORDER BY mo.observation_sequence
                LIMIT 1
            ), 0)
        """
    else:
        acceptance_pit_expression = "1"
    rows = conn.execute(
        f"""
        SELECT
            r.raw_document_id,
            r.available_at,
            r.raw_sha256,
            r.storage_path,
            r.byte_length,
            r.content_encoding,
            r.content_type,
            r.source_id,
            v.filing_raw_document_id,
            sf.accession_number,
            sf.acceptance_datetime,
            ff.document_name,
            ff.description,
            {acceptance_pit_expression}
    		AS acceptance_availability_is_point_in_time
        FROM sec_filing_file_versions AS v
        JOIN raw_source_documents AS r
          ON r.raw_document_id = v.raw_document_id
        JOIN sec_filings AS sf
          ON sf.raw_document_id = v.filing_raw_document_id
        LEFT JOIN sec_filing_files AS ff
          ON ff.filing_raw_document_id = v.filing_raw_document_id
         AND ff.sequence_number = v.sequence_number
         AND ff.document_name = v.document_name
        WHERE {' AND '.join(where)}
        ORDER BY r.raw_document_id, v.sequence_number, v.document_name
        """,
        parameters,
    ).fetchall()

    descriptors_by_id: dict[str, _DocumentDescriptor] = {}
    for row in rows:
        raw_document_id = str(row[0])
        if raw_document_id in descriptors_by_id:
            continue
        available_at = _normalized_timestamp(
            str(row[1]),
            field=f"raw_source_documents[{raw_document_id}].available_at",
        )
        acceptance_at = _normalized_timestamp(
            str(row[10]),
            field=f"sec_filings[{row[9]}].acceptance_datetime",
        )
        filing_raw_document_id = str(row[8])
        descriptors_by_id[raw_document_id] = _DocumentDescriptor(
            evidence_type="raw_source_document",
            evidence_id=raw_document_id,
            available_at=available_at,
            availability_basis=(
                "sec_acceptance_datetime"
                if available_at == acceptance_at
                else "sec_revision_retrieval_available_at"
            ),
            availability_is_point_in_time=(
                bool(row[13])
                if available_at == acceptance_at
                else True
            ),
            asset_ids=_asset_ids_for_raw(
                conn,
                raw_document_id,
                filing_raw_document_id,
            ),
            raw_document_id=raw_document_id,
            filing_raw_document_id=filing_raw_document_id,
            sec_accession_number=str(row[9]),
            acceptance_at=acceptance_at,
            raw_sha256=str(row[2]),
            storage_path=str(row[3]),
            byte_length=int(row[4]),
            content_encoding=str(row[5]) if row[5] else None,
            content_type=str(row[6]) if row[6] else None,
            source_id=str(row[7]),
            title=(
                str(row[12])
                if row[12]
                else (str(row[11]) if row[11] else None)
            ),
        )
    return list(descriptors_by_id.values())


def _sec_observations_for_raw(
    conn: sqlite3.Connection,
    *,
    raw_document_id: str,
    end: str | None,
) -> tuple[SecObservation, ...]:
    normalized_end = _timestamp_value(end) if end is not None else None
    observations: list[SecObservation] = []
    for row in conn.execute(
        """
        SELECT observation_id, observed_at
        FROM sec_filing_file_observations
        WHERE raw_document_id = ?
        """,
        (raw_document_id,),
    ):
        observed_at = _normalized_timestamp(
            str(row[1]),
            field=f"sec_observation[{row[0]}].observed_at",
        )
        if (
            normalized_end is not None
            and _timestamp_value(observed_at) > normalized_end
        ):
            continue
        observations.append(
            SecObservation(
                observation_id=str(row[0]),
                observed_at=observed_at,
            )
        )
    observations.sort(
        key=lambda item: (
            _timestamp_value(item.observed_at),
            item.observation_id,
        )
    )
    return tuple(observations)


def _hydrate_news_descriptor(
    conn: sqlite3.Connection,
    descriptor: _DocumentDescriptor,
) -> EvidenceDocument:
    row = conn.execute(
        """
        SELECT source_name, source_provider, title, summary, raw_text
        FROM news_documents
        WHERE news_id = ?
        """,
        (descriptor.evidence_id,),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            f"News disappeared during selection: {descriptor.evidence_id}"
        )
    fields: list[str] = []
    seen_fields: set[str] = set()
    for value in (row[2], row[3], row[4]):
        candidate = str(value).strip() if value else ""
        if candidate and candidate not in seen_fields:
            fields.append(candidate)
            seen_fields.add(candidate)
    source_payload = _canonical_json(
        {
            "title": row[2],
            "summary": row[3],
            "raw_text": row[4],
        }
    )
    return EvidenceDocument(
        evidence_type=descriptor.evidence_type,
        evidence_id=descriptor.evidence_id,
        available_at=descriptor.available_at,
        availability_basis=descriptor.availability_basis,
        availability_is_point_in_time=False,
        title=str(row[2]) if row[2] else None,
        text="\n".join(fields),
        content_sha256=_sha256_text(source_payload),
        source_name=str(row[0]) if row[0] else None,
        source_id=str(row[1]) if row[1] else None,
        asset_ids=descriptor.asset_ids,
    )


def _hydrate_sec_descriptor(
    conn: sqlite3.Connection,
    *,
    db: Path,
    config: ClusteringConfig,
    descriptor: _DocumentDescriptor,
    end: str | None,
) -> EvidenceDocument:
    required = {
        "raw_document_id": descriptor.raw_document_id,
        "raw_sha256": descriptor.raw_sha256,
        "storage_path": descriptor.storage_path,
        "byte_length": descriptor.byte_length,
    }
    missing = [key for key, value in required.items() if value is None]
    if missing:
        raise RuntimeError(f"Incomplete SEC descriptor: {missing}")
    text, extraction_status = _read_raw_text(
        db=db,
        storage_path=str(descriptor.storage_path),
        content_encoding=descriptor.content_encoding,
        content_type=descriptor.content_type,
        expected_sha256=str(descriptor.raw_sha256),
        expected_length=int(descriptor.byte_length),
        max_content_bytes=config.max_content_bytes,
    )
    return EvidenceDocument(
        evidence_type=descriptor.evidence_type,
        evidence_id=descriptor.evidence_id,
        available_at=descriptor.available_at,
        availability_basis=descriptor.availability_basis,
        availability_is_point_in_time=descriptor.availability_is_point_in_time,
        title=descriptor.title,
        text=text,
        content_sha256=str(descriptor.raw_sha256),
        source_name="SEC EDGAR",
        source_id=descriptor.source_id,
        asset_ids=descriptor.asset_ids,
        sec_accession_number=descriptor.sec_accession_number,
        raw_document_id=descriptor.raw_document_id,
        content_type=descriptor.content_type,
        extraction_status=extraction_status,
        sec_observations=_sec_observations_for_raw(
            conn,
            raw_document_id=str(descriptor.raw_document_id),
            end=end,
        ),
    )


def load_documents(
    conn: sqlite3.Connection,
    *,
    db: Path,
    config: ClusteringConfig,
    source: str,
    asset_id: int | None,
    ticker: str | None,
    start: str | None,
    end: str | None,
    max_documents: int,
) -> list[EvidenceDocument]:
    if source not in {"all", "news", "sec"}:
        raise ValueError("source debe ser all, news o sec")
    if max_documents <= 0:
        raise ValueError("max_documents debe ser positivo")
    resolved_asset_id = _resolve_asset_filter(
        conn,
        asset_id=asset_id,
        ticker=ticker,
    )
    normalized_start = (
        _normalized_timestamp(start, field="start") if start else None
    )
    normalized_end = _normalized_timestamp(end, field="end") if end else None
    start_value = (
        _timestamp_value(normalized_start)
        if normalized_start is not None
        else None
    )
    end_value = (
        _timestamp_value(normalized_end)
        if normalized_end is not None
        else None
    )
    if (
        start_value is not None
        and end_value is not None
        and start_value > end_value
    ):
        raise ValueError("start no puede ser posterior a end")

    descriptors: list[_DocumentDescriptor] = []
    if source in {"all", "news"}:
        descriptors.extend(
            _news_descriptors(conn, asset_id=resolved_asset_id)
        )
    if source in {"all", "sec"}:
        descriptors.extend(
            _sec_descriptors(conn, asset_id=resolved_asset_id)
        )
    descriptors = [
        descriptor
        for descriptor in descriptors
        if (
            start_value is None
            or _timestamp_value(descriptor.available_at) >= start_value
        )
        and (
            end_value is None
            or _timestamp_value(descriptor.available_at) <= end_value
        )
    ]
    descriptors.sort(
        key=lambda descriptor: (
            _timestamp_value(descriptor.available_at),
            descriptor.evidence_type,
            descriptor.evidence_id,
        )
    )

    documents: list[EvidenceDocument] = []
    for descriptor in descriptors[:max_documents]:
        if descriptor.evidence_type == "news_document":
            documents.append(_hydrate_news_descriptor(conn, descriptor))
        else:
            documents.append(
                _hydrate_sec_descriptor(
                    conn,
                    db=db,
                    config=config,
                    descriptor=descriptor,
                    end=normalized_end,
                )
            )
    return documents


def _jaccard(left: Sequence[str], right: Sequence[str]) -> float:
    left_set = set(left)
    right_set = set(right)
    if not left_set or not right_set:
        return 0.0
    return len(left_set & right_set) / len(left_set | right_set)


def _seconds_between(
    earlier: EvidenceDocument,
    later: EvidenceDocument,
) -> float:
    return (
        _timestamp_value(later.available_at)
        - _timestamp_value(earlier.available_at)
    ).total_seconds()


def _exact_key(fingerprint: DocumentFingerprint) -> str | None:
    if fingerprint.normalized_text_sha256 is not None:
        return f"text:{fingerprint.normalized_text_sha256}"
    return None


def _stable_cluster_id(
    config: ClusteringConfig,
    anchor: EvidenceDocument,
) -> str:
    material = (
        f"{config.cluster_version}\0"
        f"{anchor.evidence_type}\0{anchor.evidence_id}"
    )
    return f"ec_{_sha256_text(material)[:32]}"


def _membership_id(run_id: str, fingerprint_id: str) -> str:
    return f"ecm_{_sha256_text(f'{run_id}\0{fingerprint_id}')[:40]}"


def cluster_documents(
    documents: Sequence[EvidenceDocument],
    config: ClusteringConfig,
    *,
    run_id: str,
) -> tuple[list[Assignment], int]:
    ordered_documents = sorted(
        documents,
        key=lambda document: (
            document.available_at,
            document.evidence_type,
            document.evidence_id,
        ),
    )
    fingerprints = [
        fingerprint_document(document, config) for document in ordered_documents
    ]
    records: list[_AssignedRecord] = []
    provenance_index: dict[str, int] = {}
    exact_index: dict[str, list[int]] = {}
    block_index: dict[str, list[int]] = {}
    assignments: list[Assignment] = []
    candidate_comparisons = 0

    for decision_order, (document, fingerprint) in enumerate(
        zip(ordered_documents, fingerprints, strict=True)
    ):
        matched_index: int | None = None
        match_method = "anchor"
        similarity: float | None = None

        if document.sec_accession_number:
            matched_index = provenance_index.get(
                document.sec_accession_number
            )
            if matched_index is not None:
                match_method = "sec_accession_provenance"

        exact_key = _exact_key(fingerprint)
        if (
            matched_index is None
            and exact_key is not None
            and fingerprint.token_count >= config.min_exact_duplicate_tokens
        ):
            current_assets = set(document.asset_ids)
            for candidate_index in reversed(exact_index.get(exact_key, [])):
                candidate = records[candidate_index]
                age = _seconds_between(candidate.document, document)
                if age < 0:
                    raise RuntimeError(
                        "El orden causal contiene evidencia futura"
                    )
                if age > config.exact_duplicate_max_age_seconds:
                    continue
                if (
                    candidate.fingerprint.token_count
                    < config.min_exact_duplicate_tokens
                ):
                    continue
                if (
                    config.require_asset_overlap_for_exact_duplicate
                    and not (
                        current_assets
                        and set(candidate.document.asset_ids)
                        and current_assets.intersection(
                            candidate.document.asset_ids
                        )
                    )
                ):
                    continue
                matched_index = candidate_index
                match_method = "exact_text"
                similarity = 1.0
                break

        if (
            matched_index is None
            and fingerprint.token_count >= config.min_near_duplicate_tokens
            and fingerprint.blocking_keys
        ):
            candidate_ids: set[int] = set()
            for key in fingerprint.blocking_keys:
                candidate_ids.update(
                    block_index.get(key, [])[
                        -config.max_candidates_per_document :
                    ]
                )
            ordered_candidates = sorted(candidate_ids)[
                -config.max_candidates_per_document :
            ]
            scored: list[tuple[float, int]] = []
            current_assets = set(document.asset_ids)
            for candidate_index in ordered_candidates:
                candidate = records[candidate_index]
                age = _seconds_between(candidate.document, document)
                if age < 0:
                    raise RuntimeError(
                        "El blocking expuso evidencia futura"
                    )
                if age > config.near_duplicate_max_age_seconds:
                    continue
                if (
                    config.require_asset_overlap_for_near_duplicate
                    and not (
                        current_assets
                        and set(candidate.document.asset_ids)
                        and current_assets.intersection(
                            candidate.document.asset_ids
                        )
                    )
                ):
                    continue
                candidate_tokens = candidate.fingerprint.token_count
                if not candidate_tokens:
                    continue
                length_ratio = min(
                    fingerprint.token_count,
                    candidate_tokens,
                ) / max(fingerprint.token_count, candidate_tokens)
                if length_ratio < config.min_length_ratio:
                    continue
                candidate_comparisons += 1
                score = _jaccard(
                    fingerprint.shingle_hashes,
                    candidate.fingerprint.shingle_hashes,
                )
                if score >= config.near_duplicate_threshold:
                    scored.append((score, candidate_index))
            if scored:
                scored.sort(key=lambda item: (-item[0], item[1]))
                similarity, matched_index = scored[0]
                match_method = "near_duplicate"

        if matched_index is None:
            cluster_id = _stable_cluster_id(config, document)
            matched_evidence_key = None
            matched_membership_id = None
        else:
            matched = records[matched_index]
            cluster_id = matched.assignment.cluster_id
            matched_evidence_key = matched.document.evidence_key
            matched_membership_id = matched.assignment.membership_id

        assignment = Assignment(
            evidence_type=document.evidence_type,
            evidence_id=document.evidence_id,
            evidence_key=document.evidence_key,
            cluster_id=cluster_id,
            match_method=match_method,
            available_at=document.available_at,
            matched_evidence_key=matched_evidence_key,
            matched_membership_id=matched_membership_id,
            similarity=similarity,
            decision_order=decision_order,
            membership_id=_membership_id(run_id, fingerprint.fingerprint_id),
        )
        assignments.append(assignment)
        records.append(
            _AssignedRecord(
                document=document,
                fingerprint=fingerprint,
                assignment=assignment,
            )
        )
        record_index = len(records) - 1
        if (
            document.sec_accession_number
            and document.sec_accession_number not in provenance_index
        ):
            provenance_index[document.sec_accession_number] = record_index
        if exact_key is not None:
            exact_index.setdefault(exact_key, []).append(record_index)
        for key in fingerprint.blocking_keys:
            block_index.setdefault(key, []).append(record_index)

    return assignments, candidate_comparisons


def _register_config(
    conn: sqlite3.Connection,
    config: ClusteringConfig,
) -> None:
    payload_json = config.payload_json()
    digest = config.sha256()
    conn.execute(
        """
        INSERT OR IGNORE INTO event_clustering_configs(
            cluster_version,
            algorithm_name,
            fingerprint_version,
            normalization_version,
            configuration_sha256,
            configuration_json
        )
        VALUES (?, ?, ?, ?, ?, ?)
        """,
        (
            config.cluster_version,
            ALGORITHM_NAME,
            config.fingerprint_version,
            config.normalization_version,
            digest,
            payload_json,
        ),
    )
    row = conn.execute(
        """
        SELECT configuration_sha256, configuration_json
        FROM event_clustering_configs
        WHERE cluster_version = ?
        """,
        (config.cluster_version,),
    ).fetchone()
    if row != (digest, payload_json):
        raise RuntimeError(
            "cluster_version ya existe con otra configuración; "
            "creá una versión nueva"
        )


def _persist_fingerprint(
    conn: sqlite3.Connection,
    document: EvidenceDocument,
    fingerprint: DocumentFingerprint,
    config: ClusteringConfig,
) -> bool:
    metadata_json = _canonical_json(
        {
            "extraction_status": document.extraction_status,
            "source_id": document.source_id,
            "sec_accession_number": document.sec_accession_number,
            "no_economic_inference": True,
        }
    )
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO event_document_fingerprints(
            fingerprint_id,
            evidence_type,
            evidence_id,
            news_id,
            raw_document_id,
            fingerprint_version,
            normalized_text_sha256,
            content_sha256,
            simhash64_hex,
            shingle_hashes_json,
            blocking_keys_json,
            token_count,
            text_length,
            metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            fingerprint.fingerprint_id,
            document.evidence_type,
            document.evidence_id,
            (
                document.evidence_id
                if document.evidence_type == "news_document"
                else None
            ),
            document.raw_document_id,
            config.fingerprint_version,
            fingerprint.normalized_text_sha256,
            fingerprint.content_sha256,
            fingerprint.simhash64_hex,
            _canonical_json(fingerprint.shingle_hashes),
            _canonical_json(fingerprint.blocking_keys),
            fingerprint.token_count,
            fingerprint.text_length,
            metadata_json,
        ),
    )
    row = conn.execute(
        """
        SELECT
            evidence_type,
            evidence_id,
            fingerprint_version,
            normalized_text_sha256,
            content_sha256,
            simhash64_hex,
            shingle_hashes_json,
            blocking_keys_json,
            token_count,
            text_length
        FROM event_document_fingerprints
        WHERE fingerprint_id = ?
        """,
        (fingerprint.fingerprint_id,),
    ).fetchone()
    expected = (
        document.evidence_type,
        document.evidence_id,
        config.fingerprint_version,
        fingerprint.normalized_text_sha256,
        fingerprint.content_sha256,
        fingerprint.simhash64_hex,
        _canonical_json(fingerprint.shingle_hashes),
        _canonical_json(fingerprint.blocking_keys),
        fingerprint.token_count,
        fingerprint.text_length,
    )
    if row != expected:
        raise RuntimeError(
            f"Colisión o mutación en fingerprint {fingerprint.fingerprint_id}"
        )
    return cursor.rowcount == 1


def _persist_cluster(
    conn: sqlite3.Connection,
    *,
    assignment: Assignment,
    document: EvidenceDocument,
    config: ClusteringConfig,
) -> bool:
    metadata_json = _canonical_json(
        {
            "anchor_evidence_type": document.evidence_type,
            "anchor_evidence_id": document.evidence_id,
            "anchor_availability_basis": document.availability_basis,
            "anchor_availability_is_point_in_time": (
                document.availability_is_point_in_time
            ),
            "cluster_is_document_group_not_event": True,
            "contains_no_impact_reliability_direction_or_decay": True,
        }
    )
    cursor = conn.execute(
        """
        INSERT OR IGNORE INTO event_clusters(
            cluster_id,
            canonical_title,
            first_available_at,
            last_available_at,
            cluster_method,
            cluster_version,
            metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?)
        """,
        (
            assignment.cluster_id,
            document.title,
            document.available_at,
            document.available_at,
            ALGORITHM_NAME,
            config.cluster_version,
            metadata_json,
        ),
    )
    return cursor.rowcount == 1


def _persist_membership(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    assignment: Assignment,
    document: EvidenceDocument,
    fingerprint: DocumentFingerprint,
) -> None:
    metadata_json = _canonical_json(
        {
            "asset_ids": document.asset_ids,
            "source_name": document.source_name,
            "source_id": document.source_id,
            "sec_accession_number": document.sec_accession_number,
            "legacy_availability_assumption": (
                not document.availability_is_point_in_time
            ),
            "decision_used_only_prior_evidence": True,
        }
    )
    conn.execute(
        """
        INSERT INTO event_cluster_memberships(
            membership_id,
            clustering_run_id,
            cluster_id,
            fingerprint_id,
            evidence_type,
            evidence_id,
            evidence_available_at,
            availability_basis,
            availability_is_point_in_time,
            decision_order,
            match_method,
            matched_membership_id,
            similarity,
            metadata_json
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            assignment.membership_id,
            run_id,
            assignment.cluster_id,
            fingerprint.fingerprint_id,
            document.evidence_type,
            document.evidence_id,
            document.available_at,
            document.availability_basis,
            int(document.availability_is_point_in_time),
            assignment.decision_order,
            assignment.match_method,
            assignment.matched_membership_id,
            assignment.similarity,
            metadata_json,
        ),
    )
    if document.evidence_type == "news_document":
        conn.execute(
            """
            INSERT INTO event_cluster_news_membership_refs(
                membership_id, news_id
            )
            VALUES (?, ?)
            """,
            (assignment.membership_id, document.evidence_id),
        )
    else:
        if document.raw_document_id is None:
            raise RuntimeError("Membresía raw sin raw_document_id")
        conn.execute(
            """
            INSERT INTO event_cluster_raw_membership_refs(
                membership_id, raw_document_id
            )
            VALUES (?, ?)
            """,
            (assignment.membership_id, document.raw_document_id),
        )
        for observation in document.sec_observations:
            conn.execute(
                """
                INSERT INTO event_cluster_sec_observation_refs(
                    membership_id,
                    observation_id,
                    observed_at
                )
                VALUES (?, ?, ?)
                """,
                (
                    assignment.membership_id,
                    observation.observation_id,
                    observation.observed_at,
                ),
            )


def _assignments_from_run(
    conn: sqlite3.Connection,
    run_id: str,
) -> list[dict]:
    return [
        {
            "evidence_type": str(row[0]),
            "evidence_id": str(row[1]),
            "evidence_key": (
                ("news:" if row[0] == "news_document" else "raw:")
                + str(row[1])
            ),
            "cluster_id": str(row[2]),
            "match_method": str(row[3]),
            "available_at": str(row[4]),
            "matched_evidence_key": (
                (
                    ("news:" if row[5] == "news_document" else "raw:")
                    + str(row[6])
                )
                if row[5] is not None
                else None
            ),
            "similarity": float(row[7]) if row[7] is not None else None,
            "decision_order": int(row[8]),
            "membership_id": str(row[9]),
        }
        for row in conn.execute(
            """
            SELECT
                membership.evidence_type,
                membership.evidence_id,
                membership.cluster_id,
                membership.match_method,
                membership.evidence_available_at,
                matched.evidence_type,
                matched.evidence_id,
                membership.similarity,
                membership.decision_order,
                membership.membership_id
            FROM event_cluster_memberships AS membership
            LEFT JOIN event_cluster_memberships AS matched
              ON matched.membership_id = membership.matched_membership_id
            WHERE membership.clustering_run_id = ?
            ORDER BY membership.decision_order
            """,
            (run_id,),
        )
    ]


def _validate_runtime_schema(conn: sqlite3.Connection) -> None:
    required = {
        "assets",
        "news_documents",
        "news_assets",
        "event_clusters",
        "event_cluster_news",
        "raw_source_documents",
        "raw_document_assets",
        "sec_filings",
        "sec_filing_files",
        "sec_filing_file_versions",
        "event_clustering_configs",
        "event_clustering_runs",
        "event_document_fingerprints",
        "event_cluster_memberships",
        "event_cluster_news_membership_refs",
        "event_cluster_raw_membership_refs",
        "event_cluster_sec_observation_refs",
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
            "Aplicá migration 015 y sus dependencias antes de clusterizar. "
            f"Faltan: {missing}"
        )


def _completed_run_result(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    config: ClusteringConfig,
) -> dict:
    assignments = _assignments_from_run(conn, run_id)
    counters = conn.execute(
        """
        SELECT
            documents_considered,
            fingerprints_created,
            memberships_written,
            clusters_created,
            candidate_comparisons
        FROM event_clustering_runs
        WHERE clustering_run_id = ?
        """,
        (run_id,),
    ).fetchone()
    if counters is None:
        raise RuntimeError(f"Run disappeared: {run_id}")
    return {
        "status": "completed",
        "run_id": run_id,
        "cluster_version": config.cluster_version,
        "documents_considered": int(counters[0]),
        "fingerprints_created": int(counters[1]),
        "memberships_written": int(counters[2]),
        "clusters_created": int(counters[3]),
        "candidate_comparisons": int(counters[4]),
        "cluster_count": len(
            {value["cluster_id"] for value in assignments}
        ),
        "assignments": assignments,
        "rerun_reused": True,
    }


def _mark_run_failed(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    error: BaseException,
) -> None:
    error_json = _canonical_json(
        {
            "error_type": type(error).__name__,
            "message": str(error)[:2_000],
            "failed_at": _utc_now(),
        }
    )
    try:
        conn.execute("BEGIN IMMEDIATE")
        conn.execute(
            """
            UPDATE event_clustering_runs
            SET status = 'failed',
                finished_at = ?,
                error_json = ?
            WHERE clustering_run_id = ?
              AND status = 'running'
            """,
            (_utc_now(), error_json, run_id),
        )
        conn.commit()
    except BaseException:
        if conn.in_transaction:
            conn.rollback()
        raise


def run_clustering(
    db: Path,
    *,
    config: ClusteringConfig | None = None,
    source: str = "sec",
    asset_id: int | None = None,
    ticker: str | None = None,
    start: str | None = None,
    end: str | None = None,
    max_documents: int = 1_000,
    run_id: str | None = None,
    dry_run: bool = False,
) -> dict:
    if not db.exists():
        raise FileNotFoundError(f"DB no existe: {db}")
    selected_config = config or ClusteringConfig()
    selected_run_id = run_id or uuid.uuid4().hex
    selection = {
        "source": source,
        "asset_id": asset_id,
        "ticker": ticker.upper() if ticker else None,
        "start": (
            _normalized_timestamp(start, field="start") if start else None
        ),
        "end": _normalized_timestamp(end, field="end") if end else None,
        "max_documents": max_documents,
        "max_content_bytes": selected_config.max_content_bytes,
        "reconstruction_non_pit": source in {"news", "all"},
        "temporal_contract": (
            "sec_per_evidence_pit_flag"
            if source == "sec"
            else "legacy_news_reconstruction_not_pit_verified"
        ),
        "canonical_membership_projection": (
            "event_cluster_memberships(clustering_run_id)"
        ),
    }
    selection_json = _canonical_json(selection)

    with sqlite3.connect(db, isolation_level=None) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _validate_runtime_schema(conn)

        if dry_run:
            documents = load_documents(
                conn,
                db=db,
                config=selected_config,
                source=source,
                asset_id=asset_id,
                ticker=ticker,
                start=start,
                end=end,
                max_documents=max_documents,
            )
            assignments, candidate_comparisons = cluster_documents(
                documents,
                selected_config,
                run_id=selected_run_id,
            )
            return {
                "status": "dry_run",
                "run_id": selected_run_id,
                "cluster_version": selected_config.cluster_version,
                "documents_considered": len(documents),
                "cluster_count": len(
                    {assignment.cluster_id for assignment in assignments}
                ),
                "candidate_comparisons": candidate_comparisons,
                "assignments": [asdict(value) for value in assignments],
                "temporal_contract": selection["temporal_contract"],
                "writes": 0,
            }

        run_registered = False
        try:
            conn.execute("BEGIN IMMEDIATE")
            _register_config(conn, selected_config)
            existing = conn.execute(
                """
                SELECT cluster_version, selection_json, status
                FROM event_clustering_runs
                WHERE clustering_run_id = ?
                """,
                (selected_run_id,),
            ).fetchone()
            if existing is not None:
                if existing != (
                    selected_config.cluster_version,
                    selection_json,
                    "completed",
                ):
                    raise RuntimeError(
                        "clustering_run_id ya existe con otro contrato "
                        "o no esta completo"
                    )
                result = _completed_run_result(
                    conn,
                    run_id=selected_run_id,
                    config=selected_config,
                )
                conn.commit()
                return result

            conn.execute(
                """
                INSERT INTO event_clustering_runs(
                    clustering_run_id,
                    cluster_version,
                    started_at,
                    status,
                    as_of,
                    selection_json
                )
                VALUES (?, ?, ?, 'running', ?, ?)
                """,
                (
                    selected_run_id,
                    selected_config.cluster_version,
                    _utc_now(),
                    selection["end"],
                    selection_json,
                ),
            )
            conn.commit()
            run_registered = True

            source_data_version = int(
                conn.execute("PRAGMA data_version").fetchone()[0]
            )
            documents = load_documents(
                conn,
                db=db,
                config=selected_config,
                source=source,
                asset_id=asset_id,
                ticker=ticker,
                start=start,
                end=end,
                max_documents=max_documents,
            )
            assignments, candidate_comparisons = cluster_documents(
                documents,
                selected_config,
                run_id=selected_run_id,
            )
            fingerprints = [
                fingerprint_document(document, selected_config)
                for document in documents
            ]

            conn.execute("BEGIN IMMEDIATE")
            current_data_version = int(
                conn.execute("PRAGMA data_version").fetchone()[0]
            )
            if current_data_version != source_data_version:
                raise RuntimeError(
                    "La base cambio durante seleccion/fingerprint; "
                    "el run no se puede persistir reproduciblemente"
                )
            run_contract = conn.execute(
                """
                SELECT cluster_version, selection_json, status
                FROM event_clustering_runs
                WHERE clustering_run_id = ?
                """,
                (selected_run_id,),
            ).fetchone()
            if run_contract != (
                selected_config.cluster_version,
                selection_json,
                "running",
            ):
                raise RuntimeError("El contrato del run cambio antes de persistir")

            fingerprints_created = 0
            clusters_created = 0
            for document, fingerprint, assignment in zip(
                documents,
                fingerprints,
                assignments,
                strict=True,
            ):
                fingerprints_created += int(
                    _persist_fingerprint(
                        conn,
                        document,
                        fingerprint,
                        selected_config,
                    )
                )
                if assignment.match_method == "anchor":
                    clusters_created += int(
                        _persist_cluster(
                            conn,
                            assignment=assignment,
                            document=document,
                            config=selected_config,
                        )
                    )
                else:
                    cluster_exists = conn.execute(
                        "SELECT 1 FROM event_clusters WHERE cluster_id = ?",
                        (assignment.cluster_id,),
                    ).fetchone()
                    if cluster_exists is None:
                        raise RuntimeError(
                            "Asignacion referencia cluster no creado causalmente"
                        )
                _persist_membership(
                    conn,
                    run_id=selected_run_id,
                    assignment=assignment,
                    document=document,
                    fingerprint=fingerprint,
                )

            conn.execute(
                """
                UPDATE event_clustering_runs
                SET
                    finished_at = ?,
                    status = 'completed',
                    documents_considered = ?,
                    fingerprints_created = ?,
                    memberships_written = ?,
                    clusters_created = ?,
                    candidate_comparisons = ?,
                    error_json = NULL
                WHERE clustering_run_id = ?
                  AND status = 'running'
                """,
                (
                    _utc_now(),
                    len(documents),
                    fingerprints_created,
                    len(assignments),
                    clusters_created,
                    candidate_comparisons,
                    selected_run_id,
                ),
            )
            persisted_assignments = _assignments_from_run(
                conn,
                selected_run_id,
            )
            conn.commit()
        except BaseException as error:
            if conn.in_transaction:
                conn.rollback()
            if run_registered:
                _mark_run_failed(
                    conn,
                    run_id=selected_run_id,
                    error=error,
                )
            raise

    return {
        "status": "completed",
        "run_id": selected_run_id,
        "cluster_version": selected_config.cluster_version,
        "documents_considered": len(documents),
        "fingerprints_created": fingerprints_created,
        "memberships_written": len(assignments),
        "clusters_created": clusters_created,
        "candidate_comparisons": candidate_comparisons,
        "cluster_count": len(
            {value["cluster_id"] for value in persisted_assignments}
        ),
        "assignments": persisted_assignments,
        "temporal_contract": selection["temporal_contract"],
        "rerun_reused": False,
    }


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description=(
            "Clustering determinista y causal de news legacy y documentos SEC"
        )
    )
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--source", choices=("all", "news", "sec"), default="sec")
    parser.add_argument("--asset-id", type=int)
    parser.add_argument("--ticker")
    parser.add_argument("--start")
    parser.add_argument("--end")
    parser.add_argument("--max-documents", type=int, default=1_000)
    parser.add_argument("--run-id")
    parser.add_argument("--dry-run", action="store_true")
    parser.add_argument(
        "--cluster-version",
        default=ClusteringConfig.cluster_version,
    )
    parser.add_argument(
        "--fingerprint-version",
        default=ClusteringConfig.fingerprint_version,
    )
    parser.add_argument(
        "--near-duplicate-threshold",
        type=float,
        default=ClusteringConfig.near_duplicate_threshold,
    )
    parser.add_argument(
        "--near-duplicate-max-age-seconds",
        type=int,
        default=ClusteringConfig.near_duplicate_max_age_seconds,
    )
    parser.add_argument(
        "--exact-duplicate-max-age-seconds",
        type=int,
        default=ClusteringConfig.exact_duplicate_max_age_seconds,
    )
    parser.add_argument(
        "--min-exact-duplicate-tokens",
        type=int,
        default=ClusteringConfig.min_exact_duplicate_tokens,
    )
    parser.add_argument(
        "--max-candidates-per-document",
        type=int,
        default=ClusteringConfig.max_candidates_per_document,
    )
    parser.add_argument(
        "--max-content-bytes",
        type=int,
        default=ClusteringConfig.max_content_bytes,
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    config = ClusteringConfig(
        cluster_version=args.cluster_version,
        fingerprint_version=args.fingerprint_version,
        near_duplicate_threshold=args.near_duplicate_threshold,
        near_duplicate_max_age_seconds=(
            args.near_duplicate_max_age_seconds
        ),
        exact_duplicate_max_age_seconds=(
            args.exact_duplicate_max_age_seconds
        ),
        min_exact_duplicate_tokens=args.min_exact_duplicate_tokens,
        max_candidates_per_document=args.max_candidates_per_document,
        max_content_bytes=args.max_content_bytes,
    )
    result = run_clustering(
        args.db,
        config=config,
        source=args.source,
        asset_id=args.asset_id,
        ticker=args.ticker,
        start=args.start,
        end=args.end,
        max_documents=args.max_documents,
        run_id=args.run_id,
        dry_run=args.dry_run,
    )
    print(json.dumps(result, ensure_ascii=False, indent=2, sort_keys=True))


if __name__ == "__main__":
    main()

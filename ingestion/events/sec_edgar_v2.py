from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import os
import sqlite3
import time
import urllib.error
import urllib.request
import uuid
from dataclasses import dataclass
from datetime import datetime, timezone
from pathlib import Path
from typing import Iterable, Iterator


SOURCE_ID = "sec_edgar"
INGESTION_VERSION = "sec_metadata_v0.2.0"
DEFAULT_DB = Path("data/database/market_data_v2.db")
DEFAULT_RAW_ROOT = Path("data/raw")
DEFAULT_FORMS = (
    "8-K",
    "8-K/A",
    "10-K",
    "10-K/A",
    "10-Q",
    "10-Q/A",
    "6-K",
    "6-K/A",
    "20-F",
    "20-F/A",
    "3",
    "4",
    "5",
    "SC 13D",
    "SC 13G",
    "DEF 14A",
)
COMPANY_TICKERS_URL = "https://www.sec.gov/files/company_tickers.json"
SUBMISSIONS_URL = "https://data.sec.gov/submissions/CIK{cik}.json"
SUBMISSION_FILE_URL = "https://data.sec.gov/submissions/{name}"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def normalize_cik(value: str | int) -> str:
    digits = "".join(character for character in str(value) if character.isdigit())
    if not digits:
        raise ValueError(f"CIK inválido: {value!r}")
    return digits.zfill(10)


def normalize_timestamp(value: str | None) -> str | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=timezone.utc)
    return parsed.astimezone(timezone.utc).isoformat()


def parse_items(value: object) -> list[str]:
    if value is None:
        return []
    if isinstance(value, list):
        candidates = value
    else:
        candidates = str(value).replace(";", ",").split(",")
    return [
        str(item).strip()
        for item in candidates
        if str(item).strip()
    ]


def archive_document_url(
    cik: str,
    accession_number: str,
    document_name: str,
) -> str | None:
    if not accession_number or not document_name:
        return None
    cik_without_zeroes = str(int(normalize_cik(cik)))
    accession_path = accession_number.replace("-", "")
    return (
        "https://www.sec.gov/Archives/edgar/data/"
        f"{cik_without_zeroes}/{accession_path}/{document_name}"
    )


def iter_columnar_filings(payload: dict) -> Iterable[dict]:
    block = payload.get("filings", {}).get("recent")
    if not isinstance(block, dict):
        block = payload

    accessions = block.get("accessionNumber", [])
    if not isinstance(accessions, list):
        return

    keys = [
        key
        for key, values in block.items()
        if isinstance(values, list)
    ]

    for index, accession in enumerate(accessions):
        if not accession:
            continue
        row = {
            key: values[index] if index < len(values) else None
            for key, values in (
                (key, block[key])
                for key in keys
            )
        }
        yield row


def validate_user_agent(value: str | None) -> str:
    if not value or "@" not in value:
        raise ValueError(
            "SEC_USER_AGENT debe identificar la aplicación y un email real"
        )
    if "example.com" in value.lower():
        raise ValueError("SEC_USER_AGENT no puede usar example.com")
    return value.strip()


class SecClient:
    def __init__(
        self,
        user_agent: str,
        *,
        rate_limit_per_second: float = 5.0,
        timeout_seconds: float = 30.0,
    ) -> None:
        if rate_limit_per_second <= 0 or rate_limit_per_second > 10:
            raise ValueError("SEC rate limit debe estar entre 0 y 10 req/s")
        self.user_agent = validate_user_agent(user_agent)
        self.minimum_interval = 1.0 / rate_limit_per_second
        self.timeout_seconds = timeout_seconds
        self._last_request_started: float | None = None

    def get_bytes(self, url: str) -> bytes:
        now = time.monotonic()
        if self._last_request_started is not None:
            remaining = (
                self.minimum_interval
                - (now - self._last_request_started)
            )
            if remaining > 0:
                time.sleep(remaining)

        request = urllib.request.Request(
            url,
            headers={
                "User-Agent": self.user_agent,
                "Accept": "application/json,text/plain,*/*",
                "Accept-Encoding": "gzip",
            },
        )
        self._last_request_started = time.monotonic()

        try:
            with urllib.request.urlopen(
                request,
                timeout=self.timeout_seconds,
            ) as response:
                payload = response.read()
                if response.headers.get("Content-Encoding") == "gzip":
                    payload = gzip.decompress(payload)
                return payload
        except urllib.error.HTTPError as error:
            raise RuntimeError(
                f"SEC respondió HTTP {error.code} para {url}"
            ) from error
        except urllib.error.URLError as error:
            raise RuntimeError(
                f"No se pudo acceder a SEC para {url}: {error.reason}"
            ) from error

    def get_json(self, url: str) -> tuple[dict, bytes]:
        payload = self.get_bytes(url)
        return json.loads(payload.decode("utf-8")), payload


@dataclass(frozen=True)
class RawWrite:
    path: Path
    sha256: str
    byte_length: int


@dataclass(frozen=True)
class SubmissionPayload:
    source_url: str
    external_id: str
    storage_name: str
    parsed: dict
    payload: bytes
    retrieved_at: str


class RawStore:
    def __init__(self, root: Path) -> None:
        self.root = root

    @staticmethod
    def _verify_existing(
        destination: Path,
        payload: bytes,
        digest: str,
    ) -> RawWrite:
        try:
            with gzip.open(destination, "rb") as stream:
                existing_payload = stream.read()
        except (OSError, EOFError) as error:
            raise FileExistsError(
                f"La ruta raw existente no contiene un gzip válido: "
                f"{destination}"
            ) from error

        existing_digest = hashlib.sha256(existing_payload).hexdigest()
        existing_length = len(existing_payload)
        if (
            existing_length != len(payload)
            or existing_digest != digest
            or existing_payload != payload
        ):
            raise FileExistsError(
                "Colisión de ruta raw: "
                f"{destination}; existente sha256={existing_digest} "
                f"bytes={existing_length}, nuevo sha256={digest} "
                f"bytes={len(payload)}"
            )

        return RawWrite(
            path=destination,
            sha256=existing_digest,
            byte_length=existing_length,
        )

    def write_json(self, relative_path: Path, payload: bytes) -> RawWrite:
        digest = hashlib.sha256(payload).hexdigest()
        destination = self.root / relative_path
        destination.parent.mkdir(parents=True, exist_ok=True)

        if destination.exists():
            return self._verify_existing(destination, payload, digest)

        temporary = destination.with_suffix(
            destination.suffix + f".{uuid.uuid4().hex}.tmp"
        )
        try:
            with gzip.open(temporary, "wb") as stream:
                stream.write(payload)

            try:
                os.link(temporary, destination)
            except FileExistsError:
                return self._verify_existing(destination, payload, digest)
        finally:
            temporary.unlink(missing_ok=True)

        return RawWrite(
            path=destination,
            sha256=digest,
            byte_length=len(payload),
        )


def persist_submission_response(
    conn: sqlite3.Connection,
    store: RawStore,
    *,
    cik: str,
    source_url: str,
    external_id: str,
    storage_name: str,
    payload: bytes,
    retrieved_at: str,
) -> str:
    normalized_cik = normalize_cik(cik)
    digest = hashlib.sha256(payload).hexdigest()
    safe_stem = Path(storage_name).stem
    relative_path = (
        Path("sec")
        / "submissions"
        / normalized_cik
        / f"{safe_stem}.{digest[:16]}.json.gz"
    )
    raw = store.write_json(relative_path, payload)
    raw_document_id = hashlib.sha256(
        (
            f"{SOURCE_ID}\0{external_id}\0{raw.sha256}"
        ).encode("utf-8")
    ).hexdigest()

    conn.execute(
        """
        INSERT OR IGNORE INTO raw_source_documents(
            raw_document_id,
            source_id,
            external_id,
            document_kind,
            source_url,
            canonical_url,
            available_at,
            retrieved_at,
            content_type,
            content_encoding,
            raw_sha256,
            storage_path,
            byte_length,
            parser_status,
            metadata_json
        )
        VALUES (?, ?, ?, 'sec_submissions_json', ?, ?, ?, ?,
                'application/json', 'gzip', ?, ?, ?, 'raw', ?)
        """,
        (
            raw_document_id,
            SOURCE_ID,
            external_id,
            source_url,
            source_url,
            retrieved_at,
            retrieved_at,
            raw.sha256,
            str(raw.path),
            raw.byte_length,
            json.dumps(
                {
                    "availability_source": "retrieved_at",
                    "exact_source_bytes": True,
                },
                separators=(",", ":"),
            ),
        ),
    )
    return raw_document_id


def filing_payload(
    *,
    cik: str,
    ticker: str | None,
    entity_name: str | None,
    row: dict,
) -> dict:
    result = dict(row)
    result["cik"] = normalize_cik(cik)
    result["ticker_at_ingestion"] = ticker
    result["entity_name"] = entity_name
    result["items_normalized"] = parse_items(row.get("items"))
    return result


def persist_filing(
    conn: sqlite3.Connection,
    store: RawStore,
    *,
    cik: str,
    ticker: str | None,
    entity_name: str | None,
    row: dict,
    retrieved_at: str,
    parent_raw_document_id: str | None = None,
) -> bool:
    accession = str(row.get("accessionNumber") or "").strip()
    form = str(row.get("form") or "").strip()
    acceptance = normalize_timestamp(row.get("acceptanceDateTime"))
    primary_document = str(row.get("primaryDocument") or "").strip()

    if not accession or not form or not acceptance:
        return False

    existing = conn.execute(
        """
        SELECT raw_document_id
        FROM sec_filings
        WHERE accession_number = ?
        """,
        (accession,),
    ).fetchone()
    if existing is not None:
        if parent_raw_document_id is not None:
            conn.execute(
                """
                UPDATE raw_source_documents
                SET parent_raw_document_id =
                        COALESCE(parent_raw_document_id, ?),
                    document_kind = 'sec_filing_metadata_normalized',
                    parser_status = 'parsed',
                    parser_version = ?
                WHERE raw_document_id = ?
                """,
                (
                    parent_raw_document_id,
                    INGESTION_VERSION,
                    existing[0],
                ),
            )
        return False

    normalized_cik = normalize_cik(cik)
    payload_dict = filing_payload(
        cik=normalized_cik,
        ticker=ticker,
        entity_name=entity_name,
        row=row,
    )
    payload = json.dumps(
        payload_dict,
        sort_keys=True,
        ensure_ascii=False,
        separators=(",", ":"),
    ).encode("utf-8")

    accepted_year = acceptance[:4]
    relative_path = (
        Path("sec")
        / "filings"
        / accepted_year
        / normalized_cik
        / f"{accession}.metadata.json.gz"
    )
    raw = store.write_json(relative_path, payload)
    raw_document_id = hashlib.sha256(
        (
            f"{SOURCE_ID}\0{accession}\0{raw.sha256}"
        ).encode("utf-8")
    ).hexdigest()
    source_url = archive_document_url(
        normalized_cik,
        accession,
        primary_document,
    )

    conn.execute(
        """
        INSERT INTO raw_source_documents(
            raw_document_id,
            source_id,
            external_id,
            document_kind,
            source_url,
            canonical_url,
            published_at,
            available_at,
            retrieved_at,
            content_type,
            content_encoding,
            raw_sha256,
            storage_path,
            byte_length,
            parser_status,
            parser_version,
            parent_raw_document_id,
            metadata_json
        )
        VALUES (?, ?, ?, 'sec_filing_metadata_normalized', ?, ?, ?, ?, ?,
                'application/json', 'gzip', ?, ?, ?, 'parsed', ?, ?, ?)
        """,
        (
            raw_document_id,
            SOURCE_ID,
            accession,
            source_url,
            source_url,
            acceptance,
            acceptance,
            retrieved_at,
            raw.sha256,
            str(raw.path),
            raw.byte_length,
            INGESTION_VERSION,
            parent_raw_document_id,
            json.dumps(
                {
                    "availability_source": "acceptanceDateTime",
                    "importance_not_assigned": True,
                },
                separators=(",", ":"),
            ),
        ),
    )
    conn.execute(
        """
        INSERT INTO sec_filings(
            raw_document_id,
            cik,
            accession_number,
            form,
            filing_date,
            acceptance_datetime,
            report_date,
            primary_document,
            primary_doc_description,
            is_amendment,
            items_json,
            entity_name,
            ticker_at_ingestion,
            metadata_version
        )
        VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
        """,
        (
            raw_document_id,
            normalized_cik,
            accession,
            form,
            row.get("filingDate"),
            acceptance,
            row.get("reportDate"),
            primary_document or None,
            row.get("primaryDocDescription"),
            int(form.endswith("/A")),
            json.dumps(parse_items(row.get("items"))),
            entity_name,
            ticker,
            INGESTION_VERSION,
        ),
    )

    if ticker:
        asset = conn.execute(
            """
            SELECT asset_id
            FROM assets
            WHERE UPPER(ticker) = UPPER(?)
            ORDER BY active DESC, asset_id
            LIMIT 1
            """,
            (ticker,),
        ).fetchone()
        if asset is not None:
            conn.execute(
                """
                INSERT OR IGNORE INTO raw_document_assets(
                    raw_document_id,
                    asset_id,
                    role,
                    linking_method,
                    linking_version,
                    confidence
                )
                VALUES (?, ?, 'issuer', 'sec_ticker_cik', ?, 1.0)
                """,
                (
                    raw_document_id,
                    int(asset[0]),
                    INGESTION_VERSION,
                ),
            )

    return True


def ticker_mapping(client: SecClient) -> dict[str, dict]:
    payload, _ = client.get_json(COMPANY_TICKERS_URL)
    return {
        str(record["ticker"]).upper(): {
            "ticker": str(record["ticker"]).upper(),
            "cik": normalize_cik(record["cik_str"]),
            "title": record.get("title"),
        }
        for record in payload.values()
        if record.get("ticker") and record.get("cik_str") is not None
    }


def collect_submission_payloads(
    client: SecClient,
    *,
    cik: str,
    include_older: bool,
) -> Iterator[SubmissionPayload]:
    normalized_cik = normalize_cik(cik)
    current_url = SUBMISSIONS_URL.format(cik=normalized_cik)
    current, current_bytes = client.get_json(current_url)
    current_retrieved_at = utc_now()
    current_name = f"CIK{normalized_cik}.json"
    yield SubmissionPayload(
        source_url=current_url,
        external_id=f"submissions/{current_name}",
        storage_name=current_name,
        parsed=current,
        payload=current_bytes,
        retrieved_at=current_retrieved_at,
    )

    if include_older:
        for descriptor in current.get("filings", {}).get("files", []):
            name = descriptor.get("name")
            if not name:
                continue
            older_url = SUBMISSION_FILE_URL.format(name=name)
            older, older_bytes = client.get_json(older_url)
            older_retrieved_at = utc_now()
            yield SubmissionPayload(
                source_url=older_url,
                external_id=f"submissions/{name}",
                storage_name=Path(name).name,
                parsed=older,
                payload=older_bytes,
                retrieved_at=older_retrieved_at,
            )


def ensure_contract(conn: sqlite3.Connection) -> None:
    required = {
        "ingestion_sources",
        "source_ingestion_runs",
        "raw_source_documents",
        "raw_document_assets",
        "sec_filings",
    }
    existing = {
        row[0]
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(required - existing)
    if missing:
        raise RuntimeError(
            "Aplicá migration 011 antes de ingerir SEC. "
            f"Faltan: {missing}"
        )


def ingest_target(
    conn: sqlite3.Connection,
    store: RawStore,
    client: SecClient,
    *,
    cik: str,
    ticker: str | None,
    entity_name: str | None,
    forms: set[str],
    max_filings: int | None,
    include_older: bool,
) -> tuple[int, int]:
    discovered = 0
    inserted = 0

    for payload in collect_submission_payloads(
        client,
        cik=cik,
        include_older=include_older,
    ):
        source_raw_document_id = persist_submission_response(
            conn,
            store,
            cik=cik,
            source_url=payload.source_url,
            external_id=payload.external_id,
            storage_name=payload.storage_name,
            payload=payload.payload,
            retrieved_at=payload.retrieved_at,
        )
        parsed = payload.parsed
        if entity_name is None:
            entity_name = parsed.get("name")

        for row in iter_columnar_filings(parsed):
            form = str(row.get("form") or "")
            if form not in forms:
                continue
            discovered += 1
            if persist_filing(
                conn,
                store,
                cik=cik,
                ticker=ticker,
                entity_name=entity_name,
                row=row,
                retrieved_at=payload.retrieved_at,
                parent_raw_document_id=source_raw_document_id,
            ):
                inserted += 1
            if max_filings is not None and discovered >= max_filings:
                return discovered, inserted

    return discovered, inserted


def parse_forms(raw: str) -> set[str]:
    forms = {
        item.strip()
        for item in raw.split(",")
        if item.strip()
    }
    if not forms:
        raise ValueError("Debe seleccionarse al menos un formulario SEC")
    return forms


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--ticker", action="append", default=[])
    parser.add_argument("--cik", action="append", default=[])
    parser.add_argument(
        "--forms",
        default=",".join(DEFAULT_FORMS),
    )
    parser.add_argument("--max-filings", type=int, default=20)
    parser.add_argument("--include-older", action="store_true")
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    parser.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    parser.add_argument("--rate-limit", type=float, default=5.0)
    parser.add_argument("--user-agent")
    args = parser.parse_args()

    if not args.ticker and not args.cik:
        raise SystemExit("Indicá al menos un --ticker o --cik")
    if args.max_filings is not None and args.max_filings <= 0:
        raise SystemExit("--max-filings debe ser mayor que cero")

    user_agent = args.user_agent or os.environ.get("SEC_USER_AGENT")
    client = SecClient(
        validate_user_agent(user_agent),
        rate_limit_per_second=args.rate_limit,
    )
    forms = parse_forms(args.forms)
    store = RawStore(args.raw_root)
    run_id = uuid.uuid4().hex
    started_at = utc_now()

    with sqlite3.connect(args.db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_contract(conn)
        conn.execute(
            """
            INSERT INTO source_ingestion_runs(
                run_id, source_id, mode, started_at, status
            )
            VALUES (?, ?, ?, ?, 'running')
            """,
            (
                run_id,
                SOURCE_ID,
                "historical_with_shards"
                if args.include_older
                else "recent_metadata",
                started_at,
            ),
        )
        conn.commit()

        discovered_total = 0
        inserted_total = 0
        errors: list[dict] = []

        try:
            mapping = ticker_mapping(client) if args.ticker else {}
            targets = []

            for ticker in args.ticker:
                record = mapping.get(ticker.upper())
                if record is None:
                    errors.append(
                        {
                            "ticker": ticker,
                            "error": "ticker_not_found_in_sec_mapping",
                        }
                    )
                    continue
                targets.append(record)

            for cik in args.cik:
                targets.append(
                    {
                        "ticker": None,
                        "cik": normalize_cik(cik),
                        "title": None,
                    }
                )

            for target in targets:
                try:
                    discovered, inserted = ingest_target(
                        conn,
                        store,
                        client,
                        cik=target["cik"],
                        ticker=target["ticker"],
                        entity_name=target["title"],
                        forms=forms,
                        max_filings=args.max_filings,
                        include_older=args.include_older,
                    )
                    discovered_total += discovered
                    inserted_total += inserted
                    conn.commit()
                except Exception as error:
                    conn.rollback()
                    errors.append(
                        {
                            "ticker": target["ticker"],
                            "cik": target["cik"],
                            "error": str(error),
                        }
                    )

            status = "completed" if not errors else "completed_with_errors"
        except Exception as error:
            status = "failed"
            errors.append({"error": str(error)})

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
                discovered_total,
                inserted_total,
                discovered_total - inserted_total,
                len(errors),
                json.dumps(errors, ensure_ascii=False),
                run_id,
            ),
        )
        conn.commit()

    result = {
        "run_id": run_id,
        "status": status,
        "documents_discovered": discovered_total,
        "documents_inserted": inserted_total,
        "documents_existing": discovered_total - inserted_total,
        "errors": errors,
    }
    print(json.dumps(result, indent=2, ensure_ascii=False))

    if status == "failed":
        raise SystemExit(1)


if __name__ == "__main__":
    main()


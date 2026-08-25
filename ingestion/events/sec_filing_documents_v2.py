from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ingestion.events.sec_edgar_v2 import validate_user_agent
from ingestion.events.sec_filing_documents import (
    DEFAULT_DB,
    DEFAULT_MAX_FILE_BYTES,
    DEFAULT_MAX_FILES_PER_FILING,
    DEFAULT_MAX_FILINGS,
    DEFAULT_MAX_INDEX_BYTES,
    DEFAULT_MAX_RETRY_AFTER_SECONDS,
    DEFAULT_MAX_TOTAL_BYTES,
    DEFAULT_RATE_LIMIT,
    DEFAULT_RAW_ROOT,
    ContentAddressedRawStore,
    FilingRecord,
    SecArchiveClient,
    execute_ingestion_run,
    validate_accession,
)

SELECTION_POLICY = "latest_metadata_observation_asof_document_run_v001"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(kind: str, *parts: object) -> str:
    raw = "\0".join((kind, *(str(x) for x in parts))).encode("utf-8")
    return hashlib.sha256(raw).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(
        value, ensure_ascii=False, sort_keys=True, separators=(",", ":")
    )


def ensure_016(conn: sqlite3.Connection) -> None:
    required = {
        "sec_filing_metadata_versions",
        "sec_filing_metadata_observations",
        "sec_filing_document_metadata_selections",
    }
    existing = {
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(required - existing)
    if missing:
        raise RuntimeError(f"Falta contrato migration 016: {missing}")


def select_filings_asof(
    conn: sqlite3.Connection,
    *,
    accessions: list[str],
    max_filings: int,
    as_of: str,
    selected_metadata: dict[str, dict[str, object]],
) -> list[FilingRecord]:
    if max_filings <= 0:
        raise ValueError("max_filings debe ser positivo")

    params: list[object] = [as_of]
    accession_filter = ""
    if accessions:
        normalized = [validate_accession(x) for x in accessions]
        placeholders = ",".join("?" for _ in normalized)
        accession_filter = f"AND v.accession_number IN ({placeholders})"
        params.extend(normalized)
    params.append(max_filings)

    rows = conn.execute(
        f"""
        WITH ranked AS (
            SELECT
                o.filing_raw_document_id,
                o.metadata_observation_id,
                o.metadata_version_id,
                o.available_at,
                o.availability_is_point_in_time,
                o.observation_sequence,
                v.cik,
                v.accession_number,
                v.form,
                v.acceptance_datetime,
                v.primary_document,
                v.primary_doc_description,
                ROW_NUMBER() OVER (
                    PARTITION BY o.filing_raw_document_id
                    ORDER BY o.observation_sequence DESC
                ) AS rn
            FROM sec_filing_metadata_observations AS o
            JOIN sec_filing_metadata_versions AS v
              ON v.metadata_version_id = o.metadata_version_id
             AND v.filing_raw_document_id = o.filing_raw_document_id
            WHERE julianday(o.available_at) <= julianday(?)
              {accession_filter}
        )
        SELECT *
        FROM ranked
        WHERE rn = 1
        ORDER BY acceptance_datetime DESC, accession_number
        LIMIT ?
        """,
        params,
    ).fetchall()

    out: list[FilingRecord] = []
    for row in rows:
        (
            filing_id,
            metadata_observation_id,
            metadata_version_id,
            metadata_available_at,
            metadata_pit,
            _sequence,
            cik,
            accession,
            form,
            acceptance_datetime,
            primary_document,
            primary_description,
            _rn,
        ) = row
        filing_id = str(filing_id)
        selected_metadata[filing_id] = {
            "metadata_observation_id": str(metadata_observation_id),
            "metadata_version_id": str(metadata_version_id),
            "metadata_available_at": str(metadata_available_at),
            "metadata_pit": int(metadata_pit),
            "primary_document": str(primary_document) if primary_document else None,
            "primary_doc_description": (
                str(primary_description) if primary_description else None
            ),
        }
        out.append(
            FilingRecord(
                raw_document_id=filing_id,
                cik=str(cik),
                accession_number=str(accession),
                form=str(form),
                acceptance_datetime=str(acceptance_datetime),
                primary_document=str(primary_document) if primary_document else None,
                primary_doc_description=(
                    str(primary_description) if primary_description else None
                ),
            )
        )
    return out


def persist_selections(
    conn: sqlite3.Connection,
    *,
    run_id: str,
    selected_at: str,
    selected_metadata: dict[str, dict[str, object]],
) -> int:
    inserted = 0
    for filing_id, metadata in selected_metadata.items():
        selection_id = stable_id(
            "sec_document_metadata_selection", run_id, filing_id
        )
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO sec_filing_document_metadata_selections(
                selection_id,
                document_ingestion_run_id,
                filing_raw_document_id,
                metadata_observation_id,
                metadata_version_id,
                selected_primary_document,
                selected_primary_doc_description,
                metadata_available_at,
                selected_at,
                selection_policy,
                metadata_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                selection_id,
                run_id,
                filing_id,
                metadata["metadata_observation_id"],
                metadata["metadata_version_id"],
                metadata["primary_document"],
                metadata["primary_doc_description"],
                metadata["metadata_available_at"],
                selected_at,
                SELECTION_POLICY,
                canonical_json({
                    "metadata_availability_is_point_in_time": metadata["metadata_pit"],
                    "selection_was_asof_run_start": True,
                }),
            ),
        )
        inserted += int(cursor.rowcount == 1)
    return inserted


def run_documents(
    *,
    db: Path,
    raw_root: Path,
    accessions: list[str],
    max_filings: int,
    max_files_per_filing: int,
    max_file_bytes: int,
    max_index_bytes: int,
    max_total_bytes: int,
    max_retry_after_seconds: float,
    rate_limit: float,
    user_agent: str,
) -> dict[str, object]:
    if not db.is_file():
        raise FileNotFoundError(db)

    run_id = uuid.uuid4().hex
    started_at = utc_now()
    selected_metadata: dict[str, dict[str, object]] = {}

    def select_fn(
        conn: sqlite3.Connection,
        *,
        accessions: list[str],
        max_filings: int,
    ) -> list[FilingRecord]:
        return select_filings_asof(
            conn,
            accessions=accessions,
            max_filings=max_filings,
            as_of=started_at,
            selected_metadata=selected_metadata,
        )

    client = SecArchiveClient(
        validate_user_agent(user_agent),
        max_retry_after_seconds=max_retry_after_seconds,
        rate_limit_per_second=rate_limit,
    )
    store = ContentAddressedRawStore(raw_root)

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        ensure_016(conn)
        result = execute_ingestion_run(
            conn,
            store,
            client,
            accessions=accessions,
            max_filings=max_filings,
            max_files_per_filing=max_files_per_filing,
            max_file_bytes=max_file_bytes,
            max_index_bytes=max_index_bytes,
            max_total_bytes=max_total_bytes,
            run_id=run_id,
            started_at=started_at,
            select_fn=select_fn,
        )
        selections_written = persist_selections(
            conn,
            run_id=run_id,
            selected_at=started_at,
            selected_metadata=selected_metadata,
        )
        conn.commit()

    return {
        **result,
        "metadata_selections_written": selections_written,
        "selection_policy": SELECTION_POLICY,
    }


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--accession", action="append", default=[])
    p.add_argument("--max-filings", type=int, default=DEFAULT_MAX_FILINGS)
    p.add_argument("--max-files-per-filing", type=int, default=DEFAULT_MAX_FILES_PER_FILING)
    p.add_argument("--max-file-bytes", type=int, default=DEFAULT_MAX_FILE_BYTES)
    p.add_argument("--max-index-bytes", type=int, default=DEFAULT_MAX_INDEX_BYTES)
    p.add_argument("--max-total-bytes", type=int, default=DEFAULT_MAX_TOTAL_BYTES)
    p.add_argument("--max-retry-after-seconds", type=float, default=DEFAULT_MAX_RETRY_AFTER_SECONDS)
    p.add_argument("--rate-limit", type=float, default=DEFAULT_RATE_LIMIT)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--raw-root", type=Path, default=DEFAULT_RAW_ROOT)
    p.add_argument("--user-agent")
    a = p.parse_args()

    user_agent = a.user_agent or os.environ.get("SEC_USER_AGENT")
    result = run_documents(
        db=a.db,
        raw_root=a.raw_root,
        accessions=a.accession,
        max_filings=a.max_filings,
        max_files_per_filing=a.max_files_per_filing,
        max_file_bytes=a.max_file_bytes,
        max_index_bytes=a.max_index_bytes,
        max_total_bytes=a.max_total_bytes,
        max_retry_after_seconds=a.max_retry_after_seconds,
        rate_limit=a.rate_limit,
        user_agent=validate_user_agent(user_agent),
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))
    return 0 if result.get("status") == "completed" else 1


if __name__ == "__main__":
    raise SystemExit(main())

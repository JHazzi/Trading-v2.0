from __future__ import annotations

import argparse
import gzip
import hashlib
import json
import sqlite3
from collections.abc import Iterator
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
MIGRATION = (
    ROOT
    / "database"
    / "migrations"
    / "016_sec_filing_metadata_versioning.sql"
)
MIGRATION_VERSION = "016"
MIGRATION_NAME = "sec_filing_metadata_versioning"
MAX_METADATA_BYTES = 16 * 1024 * 1024

REQUIRED_TABLES = {
    "raw_source_documents",
    "sec_filings",
    "source_ingestion_runs",
    "schema_migrations",
}

CREATED_TABLES = {
    "sec_submission_retrievals",
    "sec_filing_metadata_versions",
    "sec_filing_metadata_observations",
    "sec_filing_document_metadata_selections",
}

REQUIRED_COLUMNS = {
    "sec_submission_retrievals": {
        "submission_retrieval_id",
        "raw_document_id",
        "ingestion_run_id",
        "external_id",
        "source_url",
        "request_identity",
        "observed_at",
        "retrieved_at",
        "provenance_status",
    },
    "sec_filing_metadata_versions": {
        "metadata_version_id",
        "filing_raw_document_id",
        "normalized_raw_document_id",
        "first_source_submissions_raw_document_id",
        "accession_number",
        "acceptance_datetime",
        "primary_document",
        "metadata_content_sha256",
        "normalized_metadata_json",
        "parser_version",
        "first_observed_at",
        "first_retrieved_at",
        "provenance_status",
    },
    "sec_filing_metadata_observations": {
        "metadata_observation_id",
        "filing_raw_document_id",
        "metadata_version_id",
        "normalized_raw_document_id",
        "source_submission_retrieval_id",
        "source_submissions_raw_document_id",
        "ingestion_run_id",
        "retrieval_identity",
        "observation_sequence",
        "state_revision_number",
        "previous_observation_id",
        "observation_kind",
        "observed_at",
        "retrieved_at",
        "available_at",
        "availability_basis",
        "availability_is_point_in_time",
        "provenance_status",
    },
    "sec_filing_document_metadata_selections": {
        "selection_id",
        "document_ingestion_run_id",
        "filing_raw_document_id",
        "metadata_observation_id",
        "metadata_version_id",
        "selected_primary_document",
        "selected_primary_doc_description",
        "metadata_available_at",
        "selected_at",
        "selection_policy",
    },
}

PRIMARY_KEYS = {
    "sec_submission_retrievals": "submission_retrieval_id",
    "sec_filing_metadata_versions": "metadata_version_id",
    "sec_filing_metadata_observations": "metadata_observation_id",
    "sec_filing_document_metadata_selections": "selection_id",
}


def _stable_id(kind: str, *parts: str) -> str:
    material = "\0".join((kind, *parts)).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _column_rows(conn: sqlite3.Connection, table: str) -> dict[str, tuple]:
    return {
        str(row[1]): row
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def _foreign_key_targets(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[2])
        for row in conn.execute(f"PRAGMA foreign_key_list({table})")
    }


def _sql_statements(sql: str) -> Iterator[str]:
    statement = ""
    for line in sql.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            candidate = statement.strip()
            statement = ""
            if candidate:
                yield candidate
    if statement.strip():
        raise RuntimeError("Migration 016 contiene SQL incompleto")


def _validate_migration_identity(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM schema_migrations WHERE version = ?",
        (MIGRATION_VERSION,),
    ).fetchone()
    if row is not None and str(row[0]) != MIGRATION_NAME:
        raise RuntimeError(
            "Colisión en schema_migrations para versión 016: "
            f"se encontró {row[0]!r}"
        )


def _raw_path(value: str) -> Path:
    path = Path(value)
    return path if path.is_absolute() else ROOT / path


def _read_normalized_json(
    storage_path: str,
    expected_sha256: str,
    expected_length: int,
) -> str:
    path = _raw_path(storage_path)
    try:
        with gzip.open(path, "rb") as stream:
            payload = stream.read(MAX_METADATA_BYTES + 1)
    except (OSError, EOFError) as error:
        raise RuntimeError(
            f"No se pudo verificar el raw normalizado SEC migrado: {path}"
        ) from error
    if len(payload) > MAX_METADATA_BYTES:
        raise RuntimeError(
            f"Raw normalizado SEC excede {MAX_METADATA_BYTES} bytes: {path}"
        )
    actual_sha256 = hashlib.sha256(payload).hexdigest()
    if actual_sha256 != expected_sha256 or len(payload) != expected_length:
        raise RuntimeError(
            "Raw normalizado SEC no coincide con su registro: "
            f"{path}; sha={actual_sha256}; bytes={len(payload)}"
        )
    try:
        parsed = json.loads(payload.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as error:
        raise RuntimeError(
            f"Raw normalizado SEC no contiene JSON UTF-8 válido: {path}"
        ) from error
    if not isinstance(parsed, dict):
        raise RuntimeError(
            f"Raw normalizado SEC no contiene un objeto JSON: {path}"
        )
    return payload.decode("utf-8")


def _backfill_submission_retrievals(conn: sqlite3.Connection) -> int:
    rows = conn.execute(
        """
        SELECT
            raw_document_id,
            external_id,
            source_url,
            retrieved_at
        FROM raw_source_documents
        WHERE source_id = 'sec_edgar'
          AND document_kind = 'sec_submissions_json'
        ORDER BY external_id, retrieved_at, raw_document_id
        """
    ).fetchall()
    inserted = 0
    for raw_document_id, external_id, source_url, retrieved_at in rows:
        raw_id = str(raw_document_id)
        request_identity = f"migration016:{raw_id}"
        retrieval_id = _stable_id(
            "sec_submission_retrieval",
            raw_id,
            request_identity,
        )
        cursor = conn.execute(
            """
            INSERT OR IGNORE INTO sec_submission_retrievals(
                submission_retrieval_id,
                raw_document_id,
                ingestion_run_id,
                external_id,
                source_url,
                request_identity,
                observed_at,
                retrieved_at,
                provenance_status,
                metadata_json
            )
            VALUES (?, ?, NULL, ?, ?, ?, ?, ?, 'migrated', ?)
            """,
            (
                retrieval_id,
                raw_id,
                str(external_id),
                str(source_url),
                request_identity,
                str(retrieved_at),
                str(retrieved_at),
                json.dumps(
                    {
                        "backfill": "migration_016",
                        "exact_historical_fetch_count_known": False,
                    },
                    separators=(",", ":"),
                    sort_keys=True,
                ),
            ),
        )
        inserted += int(cursor.rowcount == 1)
    return inserted


def _backfill_existing_filings(conn: sqlite3.Connection) -> int:
    previous_row_factory = conn.row_factory
    conn.row_factory = sqlite3.Row
    rows = conn.execute(
        """
        SELECT
            filing.raw_document_id,
            filing.cik,
            filing.accession_number,
            filing.form,
            filing.filing_date,
            filing.acceptance_datetime,
            filing.report_date,
            filing.primary_document,
            filing.primary_doc_description,
            filing.is_amendment,
            filing.items_json,
            filing.entity_name,
            filing.ticker_at_ingestion,
            filing.metadata_version,
            raw.raw_sha256,
            raw.parent_raw_document_id,
            raw.retrieved_at,
            raw.available_at,
            raw.storage_path,
            raw.byte_length
        FROM sec_filings AS filing
        JOIN raw_source_documents AS raw
          ON raw.raw_document_id = filing.raw_document_id
        ORDER BY filing.accession_number
        """
    ).fetchall()
    inserted = 0
    try:
        for row in rows:
            filing_id = str(row["raw_document_id"])
            accession = str(row["accession_number"])
            content_sha256 = str(row["raw_sha256"])
            metadata_version_id = _stable_id(
                "sec_filing_metadata_version",
                accession,
                content_sha256,
            )
            retrieved_at = str(row["retrieved_at"])
            parent_id = (
                str(row["parent_raw_document_id"])
                if row["parent_raw_document_id"]
                else None
            )
            source_retrieval_id = None
            if parent_id is not None:
                retrieval_row = conn.execute(
                    """
                    SELECT submission_retrieval_id
                    FROM sec_submission_retrievals
                    WHERE raw_document_id = ?
                    ORDER BY
                        CASE provenance_status
                            WHEN 'migrated' THEN 0 ELSE 1
                        END,
                        retrieved_at,
                        submission_retrieval_id
                    LIMIT 1
                    """,
                    (parent_id,),
                ).fetchone()
                source_retrieval_id = (
                    str(retrieval_row[0]) if retrieval_row else None
                )
            normalized_json = _read_normalized_json(
                str(row["storage_path"]),
                content_sha256,
                int(row["byte_length"]),
            )
            version_cursor = conn.execute(
                """
                INSERT OR IGNORE INTO sec_filing_metadata_versions(
                    metadata_version_id,
                    filing_raw_document_id,
                    normalized_raw_document_id,
                    first_source_submissions_raw_document_id,
                    accession_number,
                    cik,
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
                    metadata_content_sha256,
                    normalized_metadata_json,
                    parser_version,
                    first_observed_at,
                    first_retrieved_at,
                    provenance_status,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?,
                        ?, ?, ?, 'migrated', ?)
                """,
                (
                    metadata_version_id,
                    filing_id,
                    filing_id,
                    parent_id,
                    accession,
                    str(row["cik"]),
                    str(row["form"]),
                    row["filing_date"],
                    str(row["acceptance_datetime"]),
                    row["report_date"],
                    row["primary_document"],
                    row["primary_doc_description"],
                    int(row["is_amendment"]),
                    row["items_json"],
                    row["entity_name"],
                    row["ticker_at_ingestion"],
                    content_sha256,
                    normalized_json,
                    str(row["metadata_version"]),
                    retrieved_at,
                    retrieved_at,
                    json.dumps(
                        {
                            "backfill": "migration_016",
                            "canonical_initial_preserved": True,
                            "legacy_storage_path_may_not_be_content_addressed": True,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )
            inserted += int(version_cursor.rowcount == 1)
            retrieval_identity = f"migration016:{filing_id}"
            observation_id = _stable_id(
                "sec_filing_metadata_observation",
                filing_id,
                retrieval_identity,
            )
            conn.execute(
                """
                INSERT OR IGNORE INTO sec_filing_metadata_observations(
                    metadata_observation_id,
                    filing_raw_document_id,
                    metadata_version_id,
                    normalized_raw_document_id,
                    source_submissions_raw_document_id,
                    ingestion_run_id,
                    retrieval_identity,
                    observation_sequence,
                    state_revision_number,
                    previous_observation_id,
                    observation_kind,
                    observed_at,
                    retrieved_at,
                    available_at,
                    availability_basis,
                    availability_is_point_in_time,
                    provenance_status,
                    metadata_json
                )
                VALUES (?, ?, ?, ?, ?, NULL, ?, 1, 1, NULL, 'initial', ?, ?, ?,
                        'migrated_acceptance_datetime', 0, 'migrated', ?)
                """,
                (
                    observation_id,
                    filing_id,
                    metadata_version_id,
                    filing_id,
                    parent_id,
                    retrieval_identity,
                    retrieved_at,
                    retrieved_at,
                    str(row["acceptance_datetime"]),
                    json.dumps(
                        {
                            "backfill": "migration_016",
                            "best_observed_at_source": (
                                "raw_source_documents.retrieved_at"
                            ),
                            "canonical_initial_preserved": True,
                            "source_parent_missing": parent_id is None,
                        },
                        separators=(",", ":"),
                        sort_keys=True,
                    ),
                ),
            )
    finally:
        conn.row_factory = previous_row_factory
    return inserted


def _backfill_document_selections(conn: sqlite3.Connection) -> int:
    if "sec_filing_inventory_observations" not in _table_names(conn):
        return 0
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
        )
        SELECT
            lower(hex(randomblob(16))),
            inventory.retrieval_run_id,
            inventory.filing_raw_document_id,
            metadata.metadata_observation_id,
            metadata.metadata_version_id,
            version.primary_document,
            version.primary_doc_description,
            metadata.available_at,
            inventory.observed_at,
            'migrated_best_known_not_exact',
            json_object(
                'backfill', 'migration_016',
                'exact_historical_selection_known', json('false'),
                'migrated_from_inventory_observation_id',
                    inventory.observation_id
            )
        FROM sec_filing_inventory_observations AS inventory
        JOIN sec_filing_metadata_observations AS metadata
          ON metadata.filing_raw_document_id =
                inventory.filing_raw_document_id
         AND metadata.observation_sequence = 1
        JOIN sec_filing_metadata_versions AS version
          ON version.metadata_version_id = metadata.metadata_version_id
        WHERE inventory.retrieval_run_id IS NOT NULL
        ORDER BY
            inventory.retrieval_run_id,
            inventory.filing_raw_document_id
        """
    )
    return max(0, cursor.rowcount)


def _validate_contract(conn: sqlite3.Connection) -> None:
    missing = sorted(CREATED_TABLES - _table_names(conn))
    if missing:
        raise RuntimeError(f"Faltan tablas de metadata SEC 016: {missing}")

    for table, required in REQUIRED_COLUMNS.items():
        columns = _column_rows(conn, table)
        absent = sorted(required - set(columns))
        if absent:
            raise RuntimeError(f"Contrato incompleto en {table}: {absent}")
        primary_key = PRIMARY_KEYS[table]
        if int(columns[primary_key][5]) != 1:
            raise RuntimeError(f"Primary key inválida en {table}")

    expected_targets = {
        "sec_filing_metadata_versions": {
            "sec_filings",
            "raw_source_documents",
        },
        "sec_filing_metadata_observations": {
            "sec_filing_metadata_versions",
            "raw_source_documents",
            "source_ingestion_runs",
        },
        "sec_filing_document_metadata_selections": {
            "source_ingestion_runs",
            "sec_filing_metadata_observations",
        },
    }
    for table, expected in expected_targets.items():
        missing_targets = sorted(
            expected - _foreign_key_targets(conn, table)
        )
        if missing_targets:
            raise RuntimeError(
                f"Foreign keys incompletas en {table}: {missing_targets}"
            )

    filing_count = conn.execute(
        "SELECT COUNT(*) FROM sec_filings"
    ).fetchone()[0]
    initial_count = conn.execute(
        """
        SELECT COUNT(*)
        FROM sec_filing_metadata_observations
        WHERE observation_sequence = 1
          AND observation_kind = 'initial'
        """
    ).fetchone()[0]
    if initial_count != filing_count:
        raise RuntimeError(
            "Backfill 016 incompleto: "
            f"filings={filing_count}, initial_observations={initial_count}"
        )

    if "sec_filing_inventory_observations" in _table_names(conn):
        missing_selection = conn.execute(
            """
            SELECT COUNT(*)
            FROM sec_filing_inventory_observations AS inventory
            WHERE inventory.retrieval_run_id IS NOT NULL
              AND NOT EXISTS (
                  SELECT 1
                  FROM sec_filing_document_metadata_selections AS selection
                  WHERE selection.document_ingestion_run_id =
                            inventory.retrieval_run_id
                    AND selection.filing_raw_document_id =
                            inventory.filing_raw_document_id
              )
            """
        ).fetchone()[0]
        if missing_selection:
            raise RuntimeError(
                "Backfill 016 dejó selecciones documentales sin lineage: "
                f"{missing_selection}"
            )

    row = conn.execute(
        "SELECT name FROM schema_migrations WHERE version = ?",
        (MIGRATION_VERSION,),
    ).fetchone()
    if row != (MIGRATION_NAME,):
        raise RuntimeError("Migration 016 no quedó registrada correctamente")

    scoped_fk_errors: list[tuple] = []
    for table in CREATED_TABLES:
        scoped_fk_errors.extend(
            conn.execute(f"PRAGMA foreign_key_check({table})")
        )
    if scoped_fk_errors:
        raise RuntimeError(
            "Migration 016 introdujo violaciones de foreign keys: "
            f"{scoped_fk_errors[:5]}"
        )


def apply(db: Path) -> dict:
    if not db.exists():
        raise FileNotFoundError(f"DB no existe: {db}")

    sql = MIGRATION.read_text(encoding="utf-8")
    with sqlite3.connect(db, isolation_level=None) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        missing = sorted(REQUIRED_TABLES - _table_names(conn))
        if missing:
            raise RuntimeError(
                "Aplicá migration 011 antes de migration 016. "
                f"Faltan: {missing}"
            )
        _validate_migration_identity(conn)
        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in _sql_statements(sql):
                conn.execute(statement)
            versions_backfilled = _backfill_existing_filings(conn)
            selections_backfilled = _backfill_document_selections(conn)
            _validate_contract(conn)
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise

    return {
        "migration": "016_sec_filing_metadata_versioning",
        "db": str(db),
        "status": "applied",
        "versions_backfilled": versions_backfilled,
        "selections_backfilled": selections_backfilled,
        "tables": sorted(CREATED_TABLES),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    print(apply(args.db))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
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
    / "011_source_document_foundation.sql"
)

REQUIRED = [
    "ingestion_sources",
    "source_ingestion_runs",
    "source_checkpoints",
    "raw_source_documents",
    "raw_document_assets",
    "sec_filings",
    "sec_filing_files",
]


MIGRATION_VERSION = "011"
MIGRATION_NAME = "source_document_foundation"
CREATED_TABLES = {"schema_migrations", *REQUIRED}

BASE_REQUIRED_COLUMNS = {
    "schema_migrations": {
        "version",
        "name",
        "applied_at",
    },
    "ingestion_sources": {
        "source_id",
        "source_name",
        "source_type",
        "base_url",
        "terms_url",
        "access_method",
        "rate_limit_per_second",
        "enabled",
        "metadata_json",
        "created_at",
        "updated_at",
    },
    "source_ingestion_runs": {
        "run_id",
        "source_id",
        "mode",
        "started_at",
        "finished_at",
        "status",
        "checkpoint_before_json",
        "checkpoint_after_json",
        "documents_discovered",
        "documents_inserted",
        "documents_existing",
        "error_count",
        "error_json",
    },
    "source_checkpoints": {
        "source_id",
        "checkpoint_key",
        "checkpoint_value",
        "updated_at",
    },
    "raw_source_documents": {
        "raw_document_id",
        "source_id",
        "external_id",
        "document_kind",
        "source_url",
        "canonical_url",
        "published_at",
        "available_at",
        "retrieved_at",
        "modified_at",
        "content_type",
        "content_encoding",
        "raw_sha256",
        "storage_path",
        "byte_length",
        "parser_status",
        "parser_version",
        "parent_raw_document_id",
        "metadata_json",
        "created_at",
    },
    "raw_document_assets": {
        "raw_document_id",
        "asset_id",
        "role",
        "linking_method",
        "linking_version",
        "confidence",
        "metadata_json",
    },
    "sec_filings": {
        "raw_document_id",
        "cik",
        "accession_number",
        "form",
        "filing_date",
        "acceptance_datetime",
        "report_date",
        "primary_document",
        "primary_doc_description",
        "is_amendment",
        "items_json",
        "entity_name",
        "ticker_at_ingestion",
        "metadata_version",
    },
    "sec_filing_files": {
        "filing_raw_document_id",
        "sequence_number",
        "document_name",
        "document_type",
        "description",
        "source_url",
        "is_primary",
        "raw_document_id",
        "metadata_json",
    },
}

CURRENT_SEC_FILE_COLUMNS = {
    "inventory_raw_document_id",
    "declared_size_bytes",
    "declared_last_modified",
    "discovered_at",
    "last_seen_at",
    "download_status",
    "inventory_status",
    "last_attempted_at",
    "last_attempt_run_id",
    "downloaded_at",
    "selection_reason",
    "error_json",
}

PRIMARY_KEYS = {
    "schema_migrations": ("version",),
    "ingestion_sources": ("source_id",),
    "source_ingestion_runs": ("run_id",),
    "source_checkpoints": ("source_id", "checkpoint_key"),
    "raw_source_documents": ("raw_document_id",),
    "raw_document_assets": (
        "raw_document_id",
        "asset_id",
        "linking_version",
    ),
    "sec_filings": ("raw_document_id",),
    "sec_filing_files": (
        "filing_raw_document_id",
        "sequence_number",
        "document_name",
    ),
}

EXPECTED_COLUMN_TYPES = {
    "schema_migrations": {
        "version": "TEXT",
        "name": "TEXT",
        "applied_at": "TEXT",
    },
    "ingestion_sources": {
        "source_id": "TEXT",
        "source_name": "TEXT",
        "source_type": "TEXT",
        "access_method": "TEXT",
        "rate_limit_per_second": "REAL",
        "enabled": "INTEGER",
    },
    "source_ingestion_runs": {
        "run_id": "TEXT",
        "source_id": "TEXT",
        "documents_discovered": "INTEGER",
        "documents_inserted": "INTEGER",
        "documents_existing": "INTEGER",
        "error_count": "INTEGER",
    },
    "source_checkpoints": {
        "source_id": "TEXT",
        "checkpoint_key": "TEXT",
    },
    "raw_source_documents": {
        "raw_document_id": "TEXT",
        "source_id": "TEXT",
        "external_id": "TEXT",
        "available_at": "TEXT",
        "retrieved_at": "TEXT",
        "raw_sha256": "TEXT",
        "byte_length": "INTEGER",
    },
    "raw_document_assets": {
        "raw_document_id": "TEXT",
        "asset_id": "INTEGER",
        "linking_version": "TEXT",
        "confidence": "REAL",
    },
    "sec_filings": {
        "raw_document_id": "TEXT",
        "cik": "TEXT",
        "accession_number": "TEXT",
        "acceptance_datetime": "TEXT",
        "is_amendment": "INTEGER",
    },
    "sec_filing_files": {
        "filing_raw_document_id": "TEXT",
        "sequence_number": "TEXT",
        "document_name": "TEXT",
        "source_url": "TEXT",
        "is_primary": "INTEGER",
        "raw_document_id": "TEXT",
        "inventory_raw_document_id": "TEXT",
        "declared_size_bytes": "INTEGER",
        "download_status": "TEXT",
        "inventory_status": "TEXT",
        "last_attempt_run_id": "TEXT",
    },
}

NOT_NULL_COLUMNS = {
    "schema_migrations": {"name", "applied_at"},
    "ingestion_sources": {
        "source_name",
        "source_type",
        "access_method",
        "enabled",
        "created_at",
        "updated_at",
    },
    "source_ingestion_runs": {
        "source_id",
        "mode",
        "started_at",
        "status",
        "documents_discovered",
        "documents_inserted",
        "documents_existing",
        "error_count",
    },
    "source_checkpoints": {
        "source_id",
        "checkpoint_key",
        "updated_at",
    },
    "raw_source_documents": {
        "source_id",
        "external_id",
        "document_kind",
        "available_at",
        "retrieved_at",
        "raw_sha256",
        "storage_path",
        "byte_length",
        "parser_status",
        "created_at",
    },
    "raw_document_assets": {
        "raw_document_id",
        "asset_id",
        "linking_method",
        "linking_version",
    },
    "sec_filings": {
        "cik",
        "accession_number",
        "form",
        "acceptance_datetime",
        "is_amendment",
        "metadata_version",
    },
    "sec_filing_files": {
        "filing_raw_document_id",
        "sequence_number",
        "document_name",
        "source_url",
        "is_primary",
        "download_status",
        "inventory_status",
    },
}

EXPECTED_DEFAULTS = {
    ("schema_migrations", "applied_at"): "CURRENT_TIMESTAMP",
    ("ingestion_sources", "enabled"): "1",
    ("source_ingestion_runs", "documents_discovered"): "0",
    ("source_ingestion_runs", "documents_inserted"): "0",
    ("source_ingestion_runs", "documents_existing"): "0",
    ("source_ingestion_runs", "error_count"): "0",
    ("raw_source_documents", "parser_status"): "raw",
    ("sec_filings", "is_amendment"): "0",
    ("sec_filing_files", "is_primary"): "0",
    ("sec_filing_files", "download_status"): "pending",
    ("sec_filing_files", "inventory_status"): "current",
}

EXPECTED_UNIQUE_KEYS = {
    "raw_source_documents": {
        ("source_id", "external_id", "raw_sha256"),
    },
    "sec_filings": {
        ("accession_number",),
    },
}

EXPECTED_FOREIGN_KEYS = {
    "source_ingestion_runs": {
        ("source_id", "ingestion_sources", "source_id"),
    },
    "source_checkpoints": {
        ("source_id", "ingestion_sources", "source_id"),
    },
    "raw_source_documents": {
        ("source_id", "ingestion_sources", "source_id"),
        (
            "parent_raw_document_id",
            "raw_source_documents",
            "raw_document_id",
        ),
    },
    "raw_document_assets": {
        ("raw_document_id", "raw_source_documents", "raw_document_id"),
        ("asset_id", "assets", "asset_id"),
    },
    "sec_filings": {
        ("raw_document_id", "raw_source_documents", "raw_document_id"),
    },
    "sec_filing_files": {
        ("filing_raw_document_id", "sec_filings", "raw_document_id"),
        ("raw_document_id", "raw_source_documents", "raw_document_id"),
    },
}

OPTIONAL_SEC_FILE_FOREIGN_KEYS = {
    "inventory_raw_document_id": (
        "inventory_raw_document_id",
        "raw_source_documents",
        "raw_document_id",
    ),
    "last_attempt_run_id": (
        "last_attempt_run_id",
        "source_ingestion_runs",
        "run_id",
    ),
}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _column_rows(
    conn: sqlite3.Connection,
    table: str,
) -> dict[str, tuple]:
    return {
        str(row[1]): row
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def _primary_key(
    conn: sqlite3.Connection,
    table: str,
) -> tuple[str, ...]:
    rows = [
        row
        for row in conn.execute(f"PRAGMA table_info({table})")
        if int(row[5]) > 0
    ]
    return tuple(
        str(row[1])
        for row in sorted(rows, key=lambda row: int(row[5]))
    )


def _foreign_key_identities(
    conn: sqlite3.Connection,
    table: str,
) -> set[tuple[str, str, str]]:
    return {
        (str(row[3]), str(row[2]), str(row[4]))
        for row in conn.execute(f"PRAGMA foreign_key_list({table})")
    }


def _unique_column_sets(
    conn: sqlite3.Connection,
    table: str,
) -> set[tuple[str, ...]]:
    identities: set[tuple[str, ...]] = set()
    for index in conn.execute(f"PRAGMA index_list({table})"):
        if int(index[2]) != 1:
            continue
        columns = tuple(
            str(row[2])
            for row in sorted(
                conn.execute(f"PRAGMA index_info({index[1]})"),
                key=lambda row: int(row[0]),
            )
        )
        identities.add(columns)
    return identities


def _normalize_default(value: object) -> str | None:
    if value is None:
        return None
    return str(value).strip().strip("()").strip("'\"")


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
        raise RuntimeError("Migration 011 contiene SQL incompleto")


def _validate_assets_prerequisite(conn: sqlite3.Connection) -> None:
    if "assets" not in _table_names(conn):
        raise RuntimeError(
            "Aplicá el esquema base antes de migration 011: falta assets"
        )
    columns = _column_rows(conn, "assets")
    asset_id = columns.get("asset_id")
    if (
        asset_id is None
        or str(asset_id[2]).upper() != "INTEGER"
        or _primary_key(conn, "assets") != ("asset_id",)
    ):
        raise RuntimeError(
            "Prerrequisito incompatible: assets.asset_id "
            "debe ser INTEGER PRIMARY KEY"
        )


def _required_columns_for(
    table: str,
    *,
    require_current_sec_files: bool,
) -> set[str]:
    required = set(BASE_REQUIRED_COLUMNS[table])
    if table == "sec_filing_files" and require_current_sec_files:
        required.update(CURRENT_SEC_FILE_COLUMNS)
    return required


def _validate_table_contract(
    conn: sqlite3.Connection,
    table: str,
    *,
    require_current_sec_files: bool,
    phase: str,
) -> None:
    columns = _column_rows(conn, table)
    required = _required_columns_for(
        table,
        require_current_sec_files=require_current_sec_files,
    )
    missing = sorted(required - set(columns))
    if missing:
        raise RuntimeError(
            f"Contrato {phase} incompleto en {table}: faltan {missing}"
        )

    primary_key = _primary_key(conn, table)
    if primary_key != PRIMARY_KEYS[table]:
        raise RuntimeError(
            f"Primary key {phase} incompatible en {table}: "
            f"{primary_key}"
        )

    for column, expected_type in EXPECTED_COLUMN_TYPES.get(
        table,
        {},
    ).items():
        if column not in columns:
            continue
        actual_type = str(columns[column][2]).upper()
        if actual_type != expected_type:
            raise RuntimeError(
                f"Tipo {phase} inválido en {table}.{column}: "
                f"{actual_type!r}"
            )

    for column in NOT_NULL_COLUMNS.get(table, set()):
        if column not in columns:
            continue
        if int(columns[column][3]) != 1:
            raise RuntimeError(
                f"{table}.{column} debe ser NOT NULL ({phase})"
            )

    for (default_table, column), expected in EXPECTED_DEFAULTS.items():
        if default_table != table or column not in columns:
            continue
        actual = _normalize_default(columns[column][4])
        if actual != expected:
            raise RuntimeError(
                f"DEFAULT {phase} inválido en {table}.{column}: "
                f"{actual!r}"
            )

    unique_keys = _unique_column_sets(conn, table)
    missing_unique = EXPECTED_UNIQUE_KEYS.get(
        table,
        set(),
    ) - unique_keys
    if missing_unique:
        raise RuntimeError(
            f"Identidades UNIQUE {phase} incompletas en {table}: "
            f"{sorted(missing_unique)}"
        )

    expected_foreign_keys = set(
        EXPECTED_FOREIGN_KEYS.get(table, set())
    )
    if table == "sec_filing_files":
        for column, identity in OPTIONAL_SEC_FILE_FOREIGN_KEYS.items():
            if column in columns:
                expected_foreign_keys.add(identity)
    missing_foreign_keys = (
        expected_foreign_keys - _foreign_key_identities(conn, table)
    )
    if missing_foreign_keys:
        raise RuntimeError(
            f"Foreign keys {phase} incompletas en {table}: "
            f"{sorted(missing_foreign_keys)}"
        )


def _validate_migration_identity(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM schema_migrations WHERE version = ?",
        (MIGRATION_VERSION,),
    ).fetchone()
    if row is not None and str(row[0]) != MIGRATION_NAME:
        raise RuntimeError(
            "Colisión en schema_migrations para versión 011: "
            f"se encontró {row[0]!r}"
        )


def _validate_preexisting_tables(
    conn: sqlite3.Connection,
) -> bool:
    tables = _table_names(conn)
    _validate_assets_prerequisite(conn)

    if "schema_migrations" in tables:
        _validate_table_contract(
            conn,
            "schema_migrations",
            require_current_sec_files=False,
            phase="preexistente",
        )
        _validate_migration_identity(conn)

    for table in sorted(set(REQUIRED) & tables):
        _validate_table_contract(
            conn,
            table,
            require_current_sec_files=False,
            phase="preexistente",
        )

    return "sec_filing_files" in tables


def _validate_sec_source(conn: sqlite3.Connection) -> None:
    source = conn.execute(
        """
        SELECT
            source_name,
            source_type,
            base_url,
            terms_url,
            access_method,
            rate_limit_per_second,
            enabled,
            metadata_json
        FROM ingestion_sources
        WHERE source_id = 'sec_edgar'
        """
    ).fetchone()
    expected = (
        "SEC EDGAR",
        "regulator",
        "https://data.sec.gov",
        "https://www.sec.gov/about/privacy-information",
        "rest_and_bulk",
        5.0,
        1,
    )
    if source is None or tuple(source[:7]) != expected:
        raise RuntimeError(
            "La fuente sec_edgar no conserva su contrato canónico"
        )
    try:
        metadata = json.loads(str(source[7]))
    except (TypeError, json.JSONDecodeError) as exc:
        raise RuntimeError(
            "Metadata inválida para la fuente sec_edgar"
        ) from exc
    if (
        not isinstance(metadata, dict)
        or metadata.get("reliability_is_not_hardcoded") is not True
    ):
        raise RuntimeError(
            "Metadata causal inválida para la fuente sec_edgar"
        )


def _validate_contract(
    conn: sqlite3.Connection,
    *,
    sec_files_preexisting: bool,
) -> None:
    missing_tables = sorted(CREATED_TABLES - _table_names(conn))
    if missing_tables:
        raise RuntimeError(
            "Faltan tablas de Source Document Foundation: "
            f"{missing_tables}"
        )

    for table in sorted(CREATED_TABLES):
        _validate_table_contract(
            conn,
            table,
            require_current_sec_files=(
                table == "sec_filing_files"
                and not sec_files_preexisting
            ),
            phase="final",
        )

    migration_row = conn.execute(
        "SELECT name FROM schema_migrations WHERE version = ?",
        (MIGRATION_VERSION,),
    ).fetchone()
    if migration_row != (MIGRATION_NAME,):
        raise RuntimeError(
            "Migration 011 no quedó registrada correctamente"
        )

    _validate_sec_source(conn)

    scoped_fk_errors: list[tuple] = []
    for table in CREATED_TABLES:
        scoped_fk_errors.extend(
            conn.execute(f"PRAGMA foreign_key_check({table})")
        )
    if scoped_fk_errors:
        raise RuntimeError(
            "Migration 011 introdujo o encontró violaciones de "
            f"foreign keys: {scoped_fk_errors[:5]}"
        )


def apply(db: Path) -> dict:
    if not db.is_file():
        raise FileNotFoundError(f"DB no existe: {db}")

    sql = MIGRATION.read_text(encoding="utf-8")
    with sqlite3.connect(db, isolation_level=None) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        sec_files_preexisting = _validate_preexisting_tables(conn)

        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in _sql_statements(sql):
                conn.execute(statement)
            _validate_contract(
                conn,
                sec_files_preexisting=sec_files_preexisting,
            )
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise

    return {
        "migration": "011_source_document_foundation",
        "db": str(db),
        "tables": REQUIRED,
        "sec_source_registered": True,
        "status": "applied",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    print(apply(args.db))


if __name__ == "__main__":
    main()


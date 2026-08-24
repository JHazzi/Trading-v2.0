from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Iterator
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
MIGRATION = ROOT / "database" / "migrations" / "012_sec_filing_documents.sql"
MIGRATION_VERSION = "012"
MIGRATION_NAME = "sec_filing_documents"

FILE_COLUMNS = {
    "inventory_raw_document_id": (
        "TEXT REFERENCES raw_source_documents(raw_document_id) "
        "ON DELETE SET NULL"
    ),
    "declared_size_bytes": "INTEGER CHECK (declared_size_bytes >= 0)",
    "declared_last_modified": "TEXT",
    "discovered_at": "TEXT",
    "last_seen_at": "TEXT",
    "download_status": "TEXT NOT NULL DEFAULT 'pending'",
    "last_attempted_at": "TEXT",
    "downloaded_at": "TEXT",
    "selection_reason": "TEXT",
    "error_json": "TEXT",
}

REQUIRED_TABLES = {
    "raw_source_documents",
    "sec_filings",
    "sec_filing_files",
    "source_ingestion_runs",
    "schema_migrations",
}


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def column_rows(conn: sqlite3.Connection, table: str) -> dict[str, tuple]:
    return {
        str(row[1]): row
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def sql_statements(sql: str) -> Iterator[str]:
    statement = ""
    for line in sql.splitlines(keepends=True):
        statement += line
        if sqlite3.complete_statement(statement):
            candidate = statement.strip()
            statement = ""
            if candidate:
                yield candidate
    if statement.strip():
        raise RuntimeError("Migration 012 contiene SQL incompleto")


def validate_migration_identity(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM schema_migrations WHERE version = ?",
        (MIGRATION_VERSION,),
    ).fetchone()
    if row is not None and str(row[0]) != MIGRATION_NAME:
        raise RuntimeError(
            "Colisión en schema_migrations para versión 012: "
            f"se encontró {row[0]!r}"
        )


def _foreign_key_targets(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[2])
        for row in conn.execute(f"PRAGMA foreign_key_list({table})")
    }


def validate_contract(conn: sqlite3.Connection) -> None:
    expected_tables = {
        "sec_filing_inventory_snapshots",
        "sec_filing_file_versions",
    }
    missing_after = sorted(expected_tables - table_names(conn))
    if missing_after:
        raise RuntimeError(
            f"Faltan tablas de SEC filing documents: {missing_after}"
        )

    columns = column_rows(conn, "sec_filing_files")
    remaining_columns = sorted(set(FILE_COLUMNS) - set(columns))
    if remaining_columns:
        raise RuntimeError(
            "Faltan columnas de SEC filing documents: "
            f"{remaining_columns}"
        )
    expected_types = {
        "inventory_raw_document_id": "TEXT",
        "declared_size_bytes": "INTEGER",
        "declared_last_modified": "TEXT",
        "discovered_at": "TEXT",
        "last_seen_at": "TEXT",
        "download_status": "TEXT",
        "last_attempted_at": "TEXT",
        "downloaded_at": "TEXT",
        "selection_reason": "TEXT",
        "error_json": "TEXT",
    }
    malformed = [
        name
        for name, expected_type in expected_types.items()
        if str(columns[name][2]).upper() != expected_type
    ]
    download_status = columns["download_status"]
    if int(download_status[3]) != 1:
        malformed.append("download_status:not_null")
    if str(download_status[4]).strip("()'\"") != "pending":
        malformed.append("download_status:default")
    if malformed:
        raise RuntimeError(
            "Columnas incompatibles en sec_filing_files: "
            f"{sorted(malformed)}"
        )

    expected_pk = {
        "sec_filing_inventory_snapshots": {
            "filing_raw_document_id": 1,
            "inventory_raw_document_id": 2,
        },
        "sec_filing_file_versions": {
            "filing_raw_document_id": 1,
            "sequence_number": 2,
            "document_name": 3,
            "raw_document_id": 4,
        },
    }
    for table, expected in expected_pk.items():
        actual = {
            name: int(row[5])
            for name, row in column_rows(conn, table).items()
            if int(row[5]) > 0
        }
        if actual != expected:
            raise RuntimeError(
                f"Primary key incompatible en {table}: {actual}"
            )

    if "raw_source_documents" not in _foreign_key_targets(
        conn,
        "sec_filing_files",
    ):
        raise RuntimeError(
            "sec_filing_files no conserva foreign keys a raw_source_documents"
        )
    for table in expected_tables:
        targets = _foreign_key_targets(conn, table)
        if not {"source_ingestion_runs", "raw_source_documents"} <= targets:
            raise RuntimeError(f"Foreign keys incompletas en {table}: {targets}")

    row = conn.execute(
        "SELECT name FROM schema_migrations WHERE version = ?",
        (MIGRATION_VERSION,),
    ).fetchone()
    if row != (MIGRATION_NAME,):
        raise RuntimeError("Migration 012 no quedó registrada correctamente")

    scoped_fk_errors = []
    for table in expected_tables | {"sec_filing_files"}:
        scoped_fk_errors.extend(conn.execute(f"PRAGMA foreign_key_check({table})"))
    if scoped_fk_errors:
        raise RuntimeError(
            "Migration 012 introdujo violaciones de foreign keys: "
            f"{scoped_fk_errors[:5]}"
        )


def apply(db: Path) -> dict:
    if not db.exists():
        raise FileNotFoundError(f"DB no existe: {db}")

    sql = MIGRATION.read_text(encoding="utf-8")
    with sqlite3.connect(db, isolation_level=None) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        missing = sorted(REQUIRED_TABLES - table_names(conn))
        if missing:
            raise RuntimeError(
                "Aplicá migration 011 antes de migration 012. "
                f"Faltan: {missing}"
            )
        validate_migration_identity(conn)

        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_columns = column_names(conn, "sec_filing_files")
            for name, declaration in FILE_COLUMNS.items():
                if name not in existing_columns:
                    conn.execute(
                        f"ALTER TABLE sec_filing_files "
                        f"ADD COLUMN {name} {declaration}"
                    )
            for statement in sql_statements(sql):
                conn.execute(statement)
            validate_contract(conn)
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise

    return {
        "migration": "012_sec_filing_documents",
        "db": str(db),
        "status": "applied",
        "columns": sorted(FILE_COLUMNS),
        "tables": [
            "sec_filing_file_versions",
            "sec_filing_inventory_snapshots",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    print(apply(args.db))


if __name__ == "__main__":
    main()

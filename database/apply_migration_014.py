from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Iterator
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
MIGRATION = ROOT / "database" / "migrations" / "014_sec_filing_observations.sql"
MIGRATION_VERSION = "014"
MIGRATION_NAME = "sec_filing_observations"

REQUIRED_TABLES = {
    "raw_source_documents",
    "sec_filings",
    "sec_filing_files",
    "sec_filing_inventory_snapshots",
    "sec_filing_file_versions",
    "source_ingestion_runs",
    "schema_migrations",
}

FILE_COLUMNS = {
    "inventory_status": (
        "TEXT NOT NULL DEFAULT 'current' "
        "CHECK (inventory_status IN "
        "('current', 'superseded', 'unknown_migrated'))"
    ),
    "last_attempt_run_id": (
        "TEXT REFERENCES source_ingestion_runs(run_id) ON DELETE SET NULL"
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
        raise RuntimeError("Migration 014 contiene SQL incompleto")


def _validate_migration_identity(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM schema_migrations WHERE version = ?",
        (MIGRATION_VERSION,),
    ).fetchone()
    if row is not None and str(row[0]) != MIGRATION_NAME:
        raise RuntimeError(
            "Colisión en schema_migrations para versión 014: "
            f"se encontró {row[0]!r}"
        )


def _validate_contract(conn: sqlite3.Connection) -> None:
    expected_tables = {
        "sec_filing_inventory_observations",
        "sec_filing_file_observations",
    }
    missing = sorted(expected_tables - _table_names(conn))
    if missing:
        raise RuntimeError(f"Faltan tablas SEC de observaciones: {missing}")

    file_columns = _column_rows(conn, "sec_filing_files")
    inventory_status = file_columns.get("inventory_status")
    last_attempt_run = file_columns.get("last_attempt_run_id")
    if inventory_status is None or last_attempt_run is None:
        raise RuntimeError(
            "Faltan columnas de estado/intento en sec_filing_files"
        )
    if (
        str(inventory_status[2]).upper() != "TEXT"
        or int(inventory_status[3]) != 1
    ):
        raise RuntimeError("inventory_status tiene tipo o nulabilidad inválidos")
    if str(inventory_status[4]).strip("()'\"") != "current":
        raise RuntimeError("inventory_status no conserva DEFAULT current")
    if (
        str(last_attempt_run[2]).upper() != "TEXT"
        or int(last_attempt_run[3]) != 0
    ):
        raise RuntimeError("last_attempt_run_id tiene contrato inválido")
    if "source_ingestion_runs" not in {
        str(row[2])
        for row in conn.execute("PRAGMA foreign_key_list(sec_filing_files)")
    }:
        raise RuntimeError("last_attempt_run_id no conserva su foreign key")

    inventory_columns = _column_rows(
        conn,
        "sec_filing_inventory_observations",
    )
    file_observation_columns = _column_rows(
        conn,
        "sec_filing_file_observations",
    )
    if {
        "observation_id",
        "filing_raw_document_id",
        "inventory_raw_document_id",
        "observed_at",
        "parser_version",
        "retrieval_run_id",
    } - set(inventory_columns):
        raise RuntimeError("Contrato incompleto en observaciones de inventario")
    if {
        "observation_id",
        "filing_raw_document_id",
        "sequence_number",
        "document_name",
        "raw_document_id",
        "observed_at",
        "retrieval_run_id",
        "observation_status",
    } - set(file_observation_columns):
        raise RuntimeError("Contrato incompleto en observaciones de archivos")

    row = conn.execute(
        "SELECT name FROM schema_migrations WHERE version = ?",
        (MIGRATION_VERSION,),
    ).fetchone()
    if row != (MIGRATION_NAME,):
        raise RuntimeError("Migration 014 no quedó registrada correctamente")

    scoped_fk_errors = []
    for table in expected_tables:
        scoped_fk_errors.extend(conn.execute(f"PRAGMA foreign_key_check({table})"))
    if scoped_fk_errors:
        raise RuntimeError(
            "Migration 014 introdujo violaciones de foreign keys: "
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
                "Aplicá migrations 011 y 012 antes de migration 014. "
                f"Faltan: {missing}"
            )
        _validate_migration_identity(conn)

        try:
            conn.execute("BEGIN IMMEDIATE")
            existing_columns = _column_rows(conn, "sec_filing_files")
            inventory_status_added = "inventory_status" not in existing_columns
            for name, declaration in FILE_COLUMNS.items():
                if name not in existing_columns:
                    conn.execute(
                        f"ALTER TABLE sec_filing_files "
                        f"ADD COLUMN {name} {declaration}"
                    )
            if inventory_status_added:
                conn.execute(
                    """
                    UPDATE sec_filing_files
                    SET inventory_status = 'unknown_migrated'
                    """
                )
            for statement in _sql_statements(sql):
                conn.execute(statement)
            _validate_contract(conn)
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise

    return {
        "migration": "014_sec_filing_observations",
        "db": str(db),
        "status": "applied",
        "columns": sorted(FILE_COLUMNS),
        "tables": [
            "sec_filing_file_observations",
            "sec_filing_inventory_observations",
        ],
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    print(apply(args.db))


if __name__ == "__main__":
    main()

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
    / "013_daily_price_observation_foundation.sql"
)
MIGRATION_VERSION = "013"
MIGRATION_NAME = "daily_price_observation_foundation"

PREREQUISITE_COLUMNS = {
    "assets": {"asset_id"},
    "schema_migrations": {"version", "name"},
    "ingestion_sources": {"source_id", "access_method", "metadata_json"},
    "source_ingestion_runs": {"run_id", "source_id"},
}

CREATED_TABLES = {
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

REQUIRED_COLUMNS = {
    "raw_price_batches": {
        "raw_batch_id",
        "source_id",
        "source_run_id",
        "asset_id",
        "provider_symbol",
        "exchange",
        "calendar_name",
        "interval",
        "requested_start",
        "requested_end",
        "retrieved_at",
        "lineage_kind",
        "is_exact_http_response",
        "provider_library_name",
        "provider_library_version",
        "request_json",
        "raw_sha256",
        "storage_path",
        "content_type",
        "content_encoding",
        "byte_length",
        "row_count",
        "batch_version",
        "parser_version",
    },
    "raw_price_batch_retrievals": {
        "batch_retrieval_id",
        "raw_batch_id",
        "source_run_id",
        "retrieved_at",
        "request_json",
    },
    "price_bar_versions": {
        "price_bar_version_id",
        "first_raw_batch_id",
        "first_batch_retrieval_id",
        "source_id",
        "asset_id",
        "provider_symbol",
        "interval",
        "trading_day",
        "exchange",
        "calendar_name",
        "bar_start_utc",
        "bar_end_utc",
        "first_observed_at",
        "open",
        "high",
        "low",
        "close",
        "volume",
        "adjusted_close",
        "bar_content_sha256",
        "normalized_bar_json",
    },
    "price_bar_observations": {
        "price_observation_id",
        "price_bar_version_id",
        "raw_batch_id",
        "batch_retrieval_id",
        "source_id",
        "asset_id",
        "interval",
        "trading_day",
        "observed_at",
        "observed_adjusted_close",
        "available_at",
        "availability_basis",
        "point_in_time_verified",
        "observation_kind",
        "observation_sequence",
        "state_revision_number",
        "previous_observation_id",
    },
    "corporate_action_versions": {
        "corporate_action_version_id",
        "first_raw_batch_id",
        "first_batch_retrieval_id",
        "source_id",
        "asset_id",
        "action_type",
        "effective_trading_day",
        "action_time_utc",
        "is_present",
        "raw_value",
        "currency",
        "action_content_sha256",
        "normalized_action_json",
    },
    "corporate_action_observations": {
        "action_observation_id",
        "corporate_action_version_id",
        "raw_batch_id",
        "batch_retrieval_id",
        "source_id",
        "asset_id",
        "action_type",
        "effective_trading_day",
        "announcement_available_at",
        "observed_at",
        "available_at",
        "availability_basis",
        "observation_kind",
        "observation_sequence",
        "state_revision_number",
        "previous_observation_id",
    },
    "asset_identifier_history": {
        "identifier_history_id",
        "asset_id",
        "identifier_type",
        "identifier_value",
        "source_id",
        "valid_from",
        "valid_to",
        "available_at",
        "retrieved_at",
        "is_primary",
        "metadata_json",
    },
    "price_quality_runs": {
        "quality_run_id",
        "raw_batch_id",
        "batch_retrieval_id",
        "source_id",
        "quality_version",
        "started_at",
        "finished_at",
        "status",
        "configuration_json",
    },
    "price_quality_results": {
        "quality_result_id",
        "quality_run_id",
        "asset_id",
        "raw_batch_id",
        "check_name",
        "check_status",
        "observed_value",
        "expected_value",
        "details_json",
    },
}

PRIMARY_KEYS = {
    "raw_price_batches": "raw_batch_id",
    "raw_price_batch_retrievals": "batch_retrieval_id",
    "price_bar_versions": "price_bar_version_id",
    "price_bar_observations": "price_observation_id",
    "corporate_action_versions": "corporate_action_version_id",
    "corporate_action_observations": "action_observation_id",
    "asset_identifier_history": "identifier_history_id",
    "price_quality_runs": "quality_run_id",
    "price_quality_results": "quality_result_id",
}

NOT_NULL_COLUMNS = {
    "raw_price_batches": {
        "source_id",
        "asset_id",
        "interval",
        "retrieved_at",
        "raw_sha256",
        "storage_path",
    },
    "price_bar_observations": {
        "price_bar_version_id",
        "batch_retrieval_id",
        "trading_day",
        "observed_at",
        "available_at",
        "observation_kind",
    },
    "corporate_action_observations": {
        "corporate_action_version_id",
        "batch_retrieval_id",
        "effective_trading_day",
        "observed_at",
        "available_at",
        "observation_kind",
    },
    "price_quality_runs": {
        "raw_batch_id",
        "batch_retrieval_id",
        "source_id",
        "quality_version",
    },
}

EXPECTED_COLUMN_TYPES = {
    ("raw_price_batches", "raw_batch_id"): "TEXT",
    ("raw_price_batches", "asset_id"): "INTEGER",
    ("raw_price_batch_retrievals", "batch_retrieval_id"): "TEXT",
    ("price_bar_versions", "price_bar_version_id"): "TEXT",
    ("price_bar_observations", "price_observation_id"): "TEXT",
    ("price_bar_observations", "observed_adjusted_close"): "REAL",
    ("corporate_action_versions", "corporate_action_version_id"): "TEXT",
    ("corporate_action_observations", "action_observation_id"): "TEXT",
    ("asset_identifier_history", "identifier_history_id"): "TEXT",
    ("price_quality_runs", "quality_run_id"): "TEXT",
    ("price_quality_runs", "batch_retrieval_id"): "TEXT",
    ("price_quality_results", "quality_result_id"): "TEXT",
}

EXPECTED_DEFAULTS = {
    ("raw_price_batches", "is_exact_http_response"): "0",
    ("raw_price_batches", "content_type"): "application/json",
    ("raw_price_batches", "content_encoding"): "gzip",
    ("price_bar_observations", "point_in_time_verified"): "0",
    ("asset_identifier_history", "is_primary"): "0",
}

EXPECTED_FK_TARGETS = {
    "raw_price_batches": {
        "assets",
        "ingestion_sources",
        "source_ingestion_runs",
    },
    "raw_price_batch_retrievals": {
        "raw_price_batches",
        "source_ingestion_runs",
    },
    "price_bar_versions": {
        "raw_price_batches",
        "raw_price_batch_retrievals",
        "ingestion_sources",
        "assets",
    },
    "price_bar_observations": {
        "price_bar_versions",
        "raw_price_batches",
        "raw_price_batch_retrievals",
        "ingestion_sources",
        "assets",
        "price_bar_observations",
    },
    "corporate_action_versions": {
        "raw_price_batches",
        "raw_price_batch_retrievals",
        "ingestion_sources",
        "assets",
    },
    "corporate_action_observations": {
        "corporate_action_versions",
        "raw_price_batches",
        "raw_price_batch_retrievals",
        "ingestion_sources",
        "assets",
        "corporate_action_observations",
    },
    "asset_identifier_history": {"assets", "ingestion_sources"},
    "price_quality_runs": {
        "raw_price_batches",
        "raw_price_batch_retrievals",
        "ingestion_sources",
    },
    "price_quality_results": {
        "price_quality_runs",
        "assets",
        "raw_price_batches",
    },
}

EXPECTED_UNIQUE_KEYS = {
    "raw_price_batches": {
        (
            "source_id",
            "asset_id",
            "provider_symbol",
            "raw_sha256",
            "batch_version",
        )
    },
    "raw_price_batch_retrievals": {
        ("raw_batch_id", "source_run_id")
    },
    "price_bar_versions": {
        (
            "source_id",
            "asset_id",
            "interval",
            "trading_day",
            "bar_content_sha256",
        )
    },
    "price_bar_observations": {
        (
            "batch_retrieval_id",
            "asset_id",
            "interval",
            "trading_day",
            "provider_row_number",
        )
    },
    "corporate_action_versions": {
        (
            "source_id",
            "asset_id",
            "action_type",
            "effective_trading_day",
            "action_content_sha256",
        )
    },
    "corporate_action_observations": {
        (
            "batch_retrieval_id",
            "asset_id",
            "action_type",
            "effective_trading_day",
            "provider_row_number",
        )
    },
    "price_quality_runs": {
        ("batch_retrieval_id", "quality_version")
    },
    "price_quality_results": {
        ("quality_run_id", "asset_id", "check_name")
    },
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


def _foreign_key_targets(
    conn: sqlite3.Connection,
    table: str,
) -> set[str]:
    return {
        str(row[2])
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
        raise RuntimeError("Migration 013 contiene SQL incompleto")


def _validate_prerequisites(conn: sqlite3.Connection) -> None:
    tables = _table_names(conn)
    missing_tables = sorted(set(PREREQUISITE_COLUMNS) - tables)
    if missing_tables:
        raise RuntimeError(
            "Aplicá migration 011 antes de migration 013. "
            f"Faltan tablas: {missing_tables}"
        )
    for table, expected in PREREQUISITE_COLUMNS.items():
        missing = sorted(expected - set(_column_rows(conn, table)))
        if missing:
            raise RuntimeError(
                f"Prerrequisito incompleto en {table}: faltan {missing}"
            )



def _validate_preexisting_created_tables(
    conn: sqlite3.Connection,
) -> None:
    existing = CREATED_TABLES & _table_names(conn)
    for table in sorted(existing):
        columns = _column_rows(conn, table)
        missing = sorted(REQUIRED_COLUMNS[table] - set(columns))
        if missing:
            raise RuntimeError(
                f"Contrato incompleto preexistente en {table}: "
                f"faltan {missing}"
            )
        primary_key = PRIMARY_KEYS[table]
        if int(columns[primary_key][5]) != 1:
            raise RuntimeError(
                f"Primary key preexistente inválida en {table}: "
                f"{primary_key}"
            )


def _validate_migration_identity(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM schema_migrations WHERE version = ?",
        (MIGRATION_VERSION,),
    ).fetchone()
    if row is not None and str(row[0]) != MIGRATION_NAME:
        raise RuntimeError(
            "Colisión en schema_migrations para versión 013: "
            f"se encontró {row[0]!r}"
        )


def _validate_contract(conn: sqlite3.Connection) -> None:
    missing_tables = sorted(CREATED_TABLES - _table_names(conn))
    if missing_tables:
        raise RuntimeError(
            f"Faltan tablas de Daily Price Foundation: {missing_tables}"
        )

    for table, expected in REQUIRED_COLUMNS.items():
        columns = _column_rows(conn, table)
        missing = sorted(expected - set(columns))
        if missing:
            raise RuntimeError(
                f"Contrato incompleto en {table}: faltan {missing}"
            )
        primary_key = PRIMARY_KEYS[table]
        if int(columns[primary_key][5]) != 1:
            raise RuntimeError(
                f"Primary key inválida en {table}: {primary_key}"
            )
        for column in NOT_NULL_COLUMNS.get(table, set()):
            if int(columns[column][3]) != 1:
                raise RuntimeError(
                    f"{table}.{column} debe ser NOT NULL"
                )

    for (table, column), expected_type in EXPECTED_COLUMN_TYPES.items():
        actual_type = str(_column_rows(conn, table)[column][2]).upper()
        if actual_type != expected_type:
            raise RuntimeError(
                f"Tipo inválido en {table}.{column}: {actual_type!r}"
            )

    for (table, column), expected in EXPECTED_DEFAULTS.items():
        actual = _normalize_default(_column_rows(conn, table)[column][4])
        if actual != expected:
            raise RuntimeError(
                f"DEFAULT inválido en {table}.{column}: {actual!r}"
            )

    for table, expected in EXPECTED_FK_TARGETS.items():
        missing = sorted(expected - _foreign_key_targets(conn, table))
        if missing:
            raise RuntimeError(
                f"Foreign keys incompletas en {table}: {missing}"
            )

    for table, expected in EXPECTED_UNIQUE_KEYS.items():
        missing = expected - _unique_column_sets(conn, table)
        if missing:
            raise RuntimeError(
                f"Identidades UNIQUE incompletas en {table}: "
                f"{sorted(missing)}"
            )

    migration_row = conn.execute(
        "SELECT name FROM schema_migrations WHERE version = ?",
        (MIGRATION_VERSION,),
    ).fetchone()
    if migration_row != (MIGRATION_NAME,):
        raise RuntimeError("Migration 013 no quedó registrada correctamente")

    source = conn.execute(
        """
        SELECT access_method, metadata_json
        FROM ingestion_sources
        WHERE source_id = 'yahoo_finance'
        """
    ).fetchone()
    if source is None or source[0] != "python_provider_library":
        raise RuntimeError(
            "La fuente yahoo_finance no conserva su método de acceso"
        )
    metadata = json.loads(str(source[1]))
    if (
        metadata.get("lineage_kind") != "provider_library_output"
        or metadata.get("exact_http_bytes_preserved") is not False
        or metadata.get("point_in_time_history") is not False
    ):
        raise RuntimeError("Metadata causal inválida para yahoo_finance")

    scoped_fk_errors: list[tuple] = []
    for table in CREATED_TABLES:
        scoped_fk_errors.extend(
            conn.execute(f"PRAGMA foreign_key_check({table})")
        )
    if scoped_fk_errors:
        raise RuntimeError(
            "Migration 013 introdujo violaciones de foreign keys: "
            f"{scoped_fk_errors[:5]}"
        )


def apply(db: Path) -> dict:
    if not db.exists():
        raise FileNotFoundError(f"DB no existe: {db}")

    sql = MIGRATION.read_text(encoding="utf-8")
    with sqlite3.connect(db, isolation_level=None) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _validate_prerequisites(conn)
        _validate_migration_identity(conn)
        _validate_preexisting_created_tables(conn)

        try:
            conn.execute("BEGIN IMMEDIATE")
            for statement in _sql_statements(sql):
                conn.execute(statement)
            _validate_contract(conn)
            conn.commit()
        except BaseException:
            if conn.in_transaction:
                conn.rollback()
            raise

    return {
        "migration": "013_daily_price_observation_foundation",
        "db": str(db),
        "tables": sorted(CREATED_TABLES),
        "yahoo_source_registered": True,
        "legacy_price_bars_modified": False,
        "status": "applied",
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    print(apply(args.db))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import sqlite3
from collections.abc import Iterator
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
MIGRATION = (
    ROOT
    / "database"
    / "migrations"
    / "015_deterministic_event_clustering.sql"
)
MIGRATION_VERSION = "015"
MIGRATION_NAME = "deterministic_event_clustering"

REQUIRED_TABLES = {
    "assets",
    "news_documents",
    "event_clusters",
    "event_cluster_news",
    "raw_source_documents",
    "raw_document_assets",
    "sec_filings",
    "sec_filing_file_versions",
    "sec_filing_file_observations",
    "schema_migrations",
}

CREATED_TABLES = {
    "event_clustering_configs",
    "event_clustering_runs",
    "event_document_fingerprints",
    "event_cluster_memberships",
    "event_cluster_news_membership_refs",
    "event_cluster_raw_membership_refs",
    "event_cluster_sec_observation_refs",
}

CREATED_VIEWS = {
    "event_cluster_news_by_run",
    "event_clusters_by_run",
}


def _table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table'"
        )
    }


def _view_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'view'"
        )
    }


def _column_names(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
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
        raise RuntimeError("Migration 015 contiene SQL incompleto")


def _validate_migration_identity(conn: sqlite3.Connection) -> None:
    row = conn.execute(
        "SELECT name FROM schema_migrations WHERE version = ?",
        (MIGRATION_VERSION,),
    ).fetchone()
    if row is not None and str(row[0]) != MIGRATION_NAME:
        raise RuntimeError(
            "Colisión en schema_migrations para versión 015: "
            f"se encontró {row[0]!r}"
        )


def _validate_contract(conn: sqlite3.Connection) -> None:
    missing = sorted(CREATED_TABLES - _table_names(conn))
    if missing:
        raise RuntimeError(f"Faltan tablas de clustering 015: {missing}")
    missing_views = sorted(CREATED_VIEWS - _view_names(conn))
    if missing_views:
        raise RuntimeError(f"Faltan vistas de clustering 015: {missing_views}")

    required_columns = {
        "event_clustering_configs": {
            "cluster_version",
            "fingerprint_version",
            "configuration_sha256",
            "configuration_json",
        },
        "event_clustering_runs": {
            "clustering_run_id",
            "cluster_version",
            "status",
            "selection_json",
            "documents_considered",
            "candidate_comparisons",
        },
        "event_document_fingerprints": {
            "fingerprint_id",
            "evidence_type",
            "evidence_id",
            "news_id",
            "raw_document_id",
            "fingerprint_version",
            "normalized_text_sha256",
            "blocking_keys_json",
        },
        "event_cluster_memberships": {
            "membership_id",
            "clustering_run_id",
            "cluster_id",
            "fingerprint_id",
            "evidence_available_at",
            "availability_basis",
            "availability_is_point_in_time",
            "decision_order",
            "match_method",
            "matched_membership_id",
        },
        "event_cluster_sec_observation_refs": {
            "membership_id",
            "observation_id",
            "observed_at",
        },
        "event_cluster_news_by_run": {
            "clustering_run_id",
            "cluster_id",
            "news_id",
            "membership_id",
        },
        "event_clusters_by_run": {
            "clustering_run_id",
            "cluster_id",
            "first_available_at",
            "last_available_at",
            "evidence_count",
        },
    }
    for table, expected in required_columns.items():
        absent = sorted(expected - _column_names(conn, table))
        if absent:
            raise RuntimeError(
                f"Contrato incompleto en {table}: faltan {absent}"
            )

    view_row = conn.execute(
        """
        SELECT sql
        FROM sqlite_master
        WHERE type = 'view'
          AND name = 'event_clusters_by_run'
        """
    ).fetchone()
    view_sql = (
        " ".join(str(view_row[0]).lower().split())
        if view_row is not None and view_row[0]
        else ""
    )
    required_view_fragments = {
        "min(membership.evidence_available_at)",
        "max(membership.evidence_available_at)",
        "count(*)",
        "group by membership.clustering_run_id, membership.cluster_id",
    }
    missing_fragments = sorted(
        fragment
        for fragment in required_view_fragments
        if fragment not in view_sql
    )
    if missing_fragments:
        raise RuntimeError(
            "event_clusters_by_run no conserva agregacion run-scoped: "
            f"{missing_fragments}"
        )

    expected_targets = {
        "event_clustering_runs": {
            "event_clustering_configs",
        },
        "event_document_fingerprints": {
            "news_documents",
            "raw_source_documents",
        },
        "event_cluster_memberships": {
            "event_clustering_runs",
            "event_clusters",
            "event_document_fingerprints",
        },
        "event_cluster_news_membership_refs": {
            "event_cluster_memberships",
            "news_documents",
        },
        "event_cluster_raw_membership_refs": {
            "event_cluster_memberships",
            "raw_source_documents",
        },
        "event_cluster_sec_observation_refs": {
            "event_cluster_memberships",
            "sec_filing_file_observations",
        },
    }
    for table, expected in expected_targets.items():
        actual = _foreign_key_targets(conn, table)
        missing_targets = sorted(expected - actual)
        if missing_targets:
            raise RuntimeError(
                f"Foreign keys incompletas en {table}: {missing_targets}"
            )

    row = conn.execute(
        "SELECT name FROM schema_migrations WHERE version = ?",
        (MIGRATION_VERSION,),
    ).fetchone()
    if row != (MIGRATION_NAME,):
        raise RuntimeError("Migration 015 no quedó registrada correctamente")

    scoped_fk_errors: list[tuple] = []
    for table in CREATED_TABLES:
        scoped_fk_errors.extend(
            conn.execute(f"PRAGMA foreign_key_check({table})")
        )
    if scoped_fk_errors:
        raise RuntimeError(
            "Migration 015 introdujo violaciones de foreign keys: "
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
                "Aplicá migrations 010, 011, 012 y 014 antes de 015. "
                f"Faltan: {missing}"
            )
        cluster_columns = _column_names(conn, "event_clusters")
        required_cluster_columns = {
            "cluster_id",
            "first_available_at",
            "last_available_at",
            "cluster_method",
            "cluster_version",
        }
        missing_cluster_columns = sorted(
            required_cluster_columns - cluster_columns
        )
        if missing_cluster_columns:
            raise RuntimeError(
                "event_clusters no cumple el contrato canónico de migration "
                f"010; faltan {missing_cluster_columns}"
            )
        _validate_migration_identity(conn)

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
        "migration": "015_deterministic_event_clustering",
        "db": str(db),
        "status": "applied",
        "tables": sorted(CREATED_TABLES),
        "views": sorted(CREATED_VIEWS),
    }


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = parser.parse_args()
    print(apply(args.db))


if __name__ == "__main__":
    main()

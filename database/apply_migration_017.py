from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
MIGRATION = ROOT / "database" / "migrations" / "017_event_normalization.sql"

# 015/016 belong to the modern migration registry and must be present there.
EXPECTED_REGISTERED_PREDECESSORS = {
    "015": "deterministic_event_clustering",
    "016": "sec_filing_metadata_versioning",
}

# 010 was applied on some real databases before schema_migrations became the
# canonical migration ledger. In that case, recognize it structurally and
# backfill only the ledger row. Never recreate or rewrite 010 tables/data.
LEGACY_010_NAME = "event_layer"
LEGACY_010_REQUIRED_TABLES = {
    "event_clusters": {
        "cluster_id",
        "first_available_at",
        "last_available_at",
        "cluster_method",
        "cluster_version",
    },
    "event_cluster_news": {
        "cluster_id",
        "news_id",
    },
    "event_evidence": {
        "event_id",
        "available_at",
        "evidence_type",
    },
    "event_states": {
        "event_id",
        "state_time",
        "available_at",
        "feature_version",
    },
    "event_reaction_outcomes": {
        "event_id",
        "asset_id",
        "state_time",
        "reaction_version",
    },
    "event_source_knowledge": {
        "source_name",
        "model_version",
    },
}

REQUIRED_TABLES = [
    "event_normalization_configs",
    "event_normalization_runs",
    "normalized_event_identities",
    "normalized_event_versions",
    "normalized_event_observations",
    "event_cluster_event_links",
    "event_evidence_semantics",
    "normalized_event_entity_links",
    "normalized_event_asset_links",
]


def _statements(sql: str) -> list[str]:
    statements: list[str] = []
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            buf = ""
            if not stmt:
                continue
            if stmt.upper().startswith("PRAGMA FOREIGN_KEYS"):
                continue
            statements.append(stmt)
    if buf.strip():
        raise RuntimeError("SQL incompleto al final de la migración 017")
    return statements


def _migration_rows(conn: sqlite3.Connection) -> dict[str, str]:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name='schema_migrations'"
    ).fetchone()
    if not exists:
        raise RuntimeError(
            "Falta schema_migrations; la DB no parece ser Quant Market AI"
        )
    return {
        str(version): str(name)
        for version, name in conn.execute(
            "SELECT version, name FROM schema_migrations"
        )
    }


def _table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    exists = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    if exists is None:
        return set()
    return {
        str(row[1])
        for row in conn.execute(f'PRAGMA table_info("{table}")')
    }


def _validate_or_plan_010_backfill(
    conn: sqlite3.Connection,
    migrations: dict[str, str],
) -> bool:
    """
    Returns True only when the real DB has a structurally valid legacy 010
    whose schema_migrations row is missing.

    A conflicting registered identity always fails.
    """
    registered = migrations.get("010")

    if registered is not None:
        if registered != LEGACY_010_NAME:
            raise RuntimeError(
                f"Colisión de migración 010: "
                f"esperado={LEGACY_010_NAME!r}, actual={registered!r}"
            )
        return False

    missing_tables: list[str] = []
    invalid_tables: dict[str, list[str]] = {}

    for table, required_columns in LEGACY_010_REQUIRED_TABLES.items():
        columns = _table_columns(conn, table)
        if not columns:
            missing_tables.append(table)
            continue
        missing_columns = sorted(required_columns - columns)
        if missing_columns:
            invalid_tables[table] = missing_columns

    if missing_tables or invalid_tables:
        raise RuntimeError(
            "La fila 010 falta y la estructura Event Layer 010 no puede "
            "validarse de forma segura. "
            f"missing_tables={missing_tables}, "
            f"missing_columns={invalid_tables}"
        )

    return True


def apply(db: Path) -> dict:
    if not db.is_file():
        raise FileNotFoundError(f"DB inexistente: {db}")

    sql = MIGRATION.read_text(encoding="utf-8")

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        migrations = _migration_rows(conn)

        # Validate modern registered predecessors.
        for version, expected_name in EXPECTED_REGISTERED_PREDECESSORS.items():
            actual = migrations.get(version)
            if actual != expected_name:
                raise RuntimeError(
                    f"Predecesor inválido {version}: "
                    f"esperado={expected_name!r}, actual={actual!r}"
                )

        # Legacy real DBs may have 010 tables but no ledger row.
        backfill_010 = _validate_or_plan_010_backfill(conn, migrations)

        existing_017 = migrations.get("017")
        if existing_017 not in (None, "event_normalization"):
            raise RuntimeError(
                f"Colisión de migración 017: {existing_017!r}"
            )

        conn.execute("BEGIN IMMEDIATE")
        try:
            if backfill_010:
                conn.execute(
                    """
                    INSERT INTO schema_migrations(version, name)
                    VALUES ('010', 'event_layer')
                    """
                )

            for stmt in _statements(sql):
                conn.execute(stmt)

            missing = [
                table
                for table in REQUIRED_TABLES
                if conn.execute(
                    """
                    SELECT 1
                    FROM sqlite_master
                    WHERE type='table' AND name=?
                    """,
                    (table,),
                ).fetchone()
                is None
            ]
            if missing:
                raise RuntimeError(f"Faltan tablas 017: {missing}")

            row = conn.execute(
                "SELECT name FROM schema_migrations WHERE version='017'"
            ).fetchone()
            if row is None or row[0] != "event_normalization":
                raise RuntimeError(f"Identidad 017 inválida: {row}")

            row_010 = conn.execute(
                "SELECT name FROM schema_migrations WHERE version='010'"
            ).fetchone()
            if row_010 is None or row_010[0] != "event_layer":
                raise RuntimeError(
                    f"Identidad 010 inválida después del preflight: {row_010}"
                )

            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "migration": "017_event_normalization",
        "db": str(db),
        "status": "applied",
        "legacy_010_registry_backfilled": backfill_010,
        "tables": REQUIRED_TABLES,
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()
    print(apply(args.db))


if __name__ == "__main__":
    main()

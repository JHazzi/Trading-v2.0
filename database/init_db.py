#!/usr/bin/env python3
"""Initialize a fresh Quant Market AI database at the canonical schema."""

from __future__ import annotations

import argparse
import os
import sqlite3
import uuid
from pathlib import Path


CANONICAL_MIGRATIONS = (
    ("001", "001_legacy_archive.sql"),
    ("002", "002_market_foundation.sql"),
    ("003", "003_target_quality.sql"),
    ("004", "004_target_idempotency.sql"),
    ("005", "005_target_dedup.sql"),
    ("006", "006_market_sessions.sql"),
    ("007", "007_market_state_v002.sql"),
    ("008", "008_market_state_v002_390m.sql"),
    ("009", "009_asset_universe_membership.sql"),
    ("010", "010_event_layer.sql"),
    ("011", "011_source_document_foundation.sql"),
    ("012", "012_sec_filing_documents.sql"),
    ("013", "013_daily_price_observation_foundation.sql"),
    ("014", "014_sec_filing_observations.sql"),
    ("015", "015_deterministic_event_clustering.sql"),
    ("016", "016_sec_filing_metadata_versioning.sql"),
    ("017", "017_event_normalization.sql"),
    ("018", "018_daily_price_asof.sql"),
    ("019", "019_event_brain_v001.sql"),
)

REQUIRED_CURRENT_TABLES = {
    "asset_universe_membership",
    "event_clusters",
    "event_evidence",
    "event_states",
    "event_reaction_outcomes",
    "ingestion_sources",
    "raw_source_documents",
    "sec_filings",
    "sec_filing_files",
    "sec_filing_inventory_snapshots",
    "sec_filing_file_versions",
    "source_ingestion_runs",
    "raw_price_batches",
    "raw_price_batch_retrievals",
    "price_bar_versions",
    "price_bar_observations",
    "corporate_action_versions",
    "corporate_action_observations",
    "asset_identifier_history",
    "price_quality_runs",
    "price_quality_results",
    "sec_filing_inventory_observations",
    "sec_filing_file_observations",
    "event_clustering_configs",
    "event_clustering_runs",
    "event_document_fingerprints",
    "event_cluster_memberships",
    "event_cluster_news_membership_refs",
    "event_cluster_raw_membership_refs",
    "event_cluster_sec_observation_refs",
    "sec_submission_retrievals",
    "sec_filing_metadata_versions",
    "sec_filing_metadata_observations",
    "sec_filing_document_metadata_selections",
    "event_normalization_configs",
    "event_normalization_runs",
    "normalized_event_identities",
    "normalized_event_versions",
    "normalized_event_observations",
    "event_cluster_event_links",
    "event_evidence_semantics",
    "normalized_event_entity_links",
    "normalized_event_asset_links",
    "daily_price_asof_configs",
    "event_state_feature_configs",
    "normalized_event_state_snapshots",
    "normalized_event_reaction_labels",
    "event_brain_training_runs",
}


def _migration_scripts(migrations_dir: Path) -> list[tuple[str, Path]]:
    scripts = [
        (version, migrations_dir / filename)
        for version, filename in CANONICAL_MIGRATIONS
    ]
    missing = [str(path) for _, path in scripts if not path.is_file()]
    if missing:
        raise FileNotFoundError(
            f"Faltan migraciones canónicas: {missing}"
        )
    return scripts


def _logical_migration_name(version: str, migration_path: Path) -> str:
    prefix = f"{version}_"
    stem = migration_path.stem
    if not stem.startswith(prefix) or stem == prefix:
        raise ValueError(
            "Nombre de migración canónica inválido: "
            f"{migration_path.name!r} para versión {version!r}"
        )
    return stem[len(prefix):]


def initialize_database(
    schema_path: Path,
    db_path: Path,
    migrations_dir: Path | None = None,
) -> tuple[str, ...]:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if db_path.exists():
        raise FileExistsError(
            f"La base ya existe: {db_path}. "
            "No se sobrescribe automáticamente. Elegí otra ruta o "
            "eliminala de forma explícita."
        )

    schema = schema_path.read_text(encoding="utf-8")
    migration_scripts = _migration_scripts(
        migrations_dir or schema_path.parent / "migrations"
    )
    temporary_db = db_path.with_name(
        f".{db_path.name}.{uuid.uuid4().hex}.building"
    )
    applied: list[str] = []
    conn = sqlite3.connect(temporary_db)
    try:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(schema)
        conn.execute(
            """
            CREATE TABLE IF NOT EXISTS schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
            )
            """
        )

        for version, migration_path in migration_scripts:
            logical_name = _logical_migration_name(
                version,
                migration_path,
            )
            conn.executescript(
                migration_path.read_text(encoding="utf-8")
            )
            migration_row = conn.execute(
                "SELECT name FROM schema_migrations WHERE version = ?",
                (version,),
            ).fetchone()
            if migration_row is None:
                conn.execute(
                    """
                    INSERT INTO schema_migrations(version, name)
                    VALUES (?, ?)
                    """,
                    (version, logical_name),
                )
            elif str(migration_row[0]) != logical_name:
                raise RuntimeError(
                    "Colisión en schema_migrations para versión "
                    f"{version}: se encontró {migration_row[0]!r}; "
                    f"se esperaba {logical_name!r}"
                )
            applied.append(version)

        existing = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        missing_tables = sorted(REQUIRED_CURRENT_TABLES - existing)
        if missing_tables:
            raise RuntimeError(
                f"Bootstrap incompleto; faltan tablas: {missing_tables}"
            )

        conn.commit()
    except Exception:
        conn.rollback()
        conn.close()
        temporary_db.unlink(missing_ok=True)
        raise
    finally:
        conn.close()

    try:
        os.replace(temporary_db, db_path)
    except Exception:
        temporary_db.unlink(missing_ok=True)
        raise

    return tuple(applied)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).with_name("schema.sql"),
        help="Ruta a schema.sql",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(__file__).parents[1] / "data" / "database" / "market_data_v2.db",
        help="Ruta de la nueva base SQLite",
    )
    parser.add_argument(
        "--migrations-dir",
        type=Path,
        default=Path(__file__).with_name("migrations"),
        help="Directorio de migraciones canónicas",
    )
    args = parser.parse_args()

    applied = initialize_database(
        args.schema,
        args.db,
        args.migrations_dir,
    )
    print(
        f"Base creada correctamente: {args.db} "
        f"(migraciones: {','.join(applied)})"
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

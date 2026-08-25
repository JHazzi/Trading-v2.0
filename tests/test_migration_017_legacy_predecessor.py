import sqlite3
from pathlib import Path

from database.apply_migration_017 import apply


def make_base(path: Path, *, register_010: bool) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE schema_migrations(
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
            INSERT INTO schema_migrations VALUES ('015','deterministic_event_clustering');
            INSERT INTO schema_migrations VALUES ('016','sec_filing_metadata_versioning');

            CREATE TABLE assets(asset_id INTEGER PRIMARY KEY, ticker TEXT);
            CREATE TABLE entities(entity_id INTEGER PRIMARY KEY, canonical_name TEXT);

            CREATE TABLE event_clusters(
                cluster_id TEXT PRIMARY KEY,
                canonical_title TEXT,
                first_available_at TEXT NOT NULL,
                last_available_at TEXT NOT NULL,
                cluster_method TEXT NOT NULL,
                cluster_version TEXT NOT NULL,
                metadata_json TEXT
            );
            CREATE TABLE event_cluster_news(
                cluster_id TEXT NOT NULL,
                news_id TEXT NOT NULL,
                PRIMARY KEY(cluster_id, news_id)
            );
            CREATE TABLE event_evidence(
                evidence_id INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL,
                news_id TEXT,
                available_at TEXT NOT NULL,
                evidence_type TEXT NOT NULL
            );
            CREATE TABLE event_states(
                event_state_id INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL,
                asset_id INTEGER,
                state_time TEXT NOT NULL,
                available_at TEXT NOT NULL,
                feature_version TEXT NOT NULL
            );
            CREATE TABLE event_reaction_outcomes(
                reaction_id INTEGER PRIMARY KEY,
                event_id TEXT NOT NULL,
                asset_id INTEGER NOT NULL,
                state_time TEXT NOT NULL,
                reaction_version TEXT NOT NULL
            );
            CREATE TABLE event_source_knowledge(
                knowledge_id INTEGER PRIMARY KEY,
                source_name TEXT NOT NULL,
                model_version TEXT NOT NULL
            );

            CREATE TABLE event_clustering_configs(
                cluster_version TEXT PRIMARY KEY
            );
            CREATE TABLE event_clustering_runs(
                clustering_run_id TEXT PRIMARY KEY,
                cluster_version TEXT NOT NULL
            );
            CREATE TABLE event_document_fingerprints(
                fingerprint_id TEXT PRIMARY KEY
            );
            CREATE TABLE event_cluster_memberships(
                membership_id TEXT PRIMARY KEY,
                clustering_run_id TEXT NOT NULL,
                cluster_id TEXT NOT NULL,
                fingerprint_id TEXT NOT NULL
            );
            """
        )
        if register_010:
            conn.execute(
                "INSERT INTO schema_migrations VALUES ('010','event_layer')"
            )


def test_017_accepts_registered_010(tmp_path):
    db = tmp_path / "db.sqlite"
    make_base(db, register_010=True)

    result = apply(db)

    assert result["legacy_010_registry_backfilled"] is False
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT name FROM schema_migrations WHERE version='017'"
        ).fetchone() == ("event_normalization",)


def test_017_backfills_structurally_valid_legacy_010(tmp_path):
    db = tmp_path / "db.sqlite"
    make_base(db, register_010=False)

    result = apply(db)

    assert result["legacy_010_registry_backfilled"] is True
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT name FROM schema_migrations WHERE version='010'"
        ).fetchone() == ("event_layer",)
        assert conn.execute(
            "SELECT name FROM schema_migrations WHERE version='017'"
        ).fetchone() == ("event_normalization",)


def test_017_rerun_is_idempotent_after_legacy_backfill(tmp_path):
    db = tmp_path / "db.sqlite"
    make_base(db, register_010=False)

    first = apply(db)
    second = apply(db)

    assert first["legacy_010_registry_backfilled"] is True
    assert second["legacy_010_registry_backfilled"] is False

    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version='010'"
        ).fetchone()[0] == 1
        assert conn.execute(
            "SELECT COUNT(*) FROM schema_migrations WHERE version='017'"
        ).fetchone()[0] == 1

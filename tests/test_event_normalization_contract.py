import sqlite3
from pathlib import Path

from database.apply_migration_017 import apply


def make_base(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.executescript(
            """
            CREATE TABLE schema_migrations(
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );
            INSERT INTO schema_migrations VALUES ('010','event_layer');
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


def test_017_applies_and_is_idempotent(tmp_path):
    db = tmp_path / "db.sqlite"
    make_base(db)
    first = apply(db)
    second = apply(db)
    assert first["status"] == "applied"
    assert second["status"] == "applied"

    with sqlite3.connect(db) as conn:
        row = conn.execute(
            "SELECT name FROM schema_migrations WHERE version='017'"
        ).fetchone()
        assert row == ("event_normalization",)


def test_event_occurrence_may_precede_information_availability(tmp_path):
    db = tmp_path / "db.sqlite"
    make_base(db)
    apply(db)

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        conn.execute(
            "INSERT INTO event_clustering_configs VALUES ('c1')"
        )
        conn.execute(
            "INSERT INTO event_clustering_runs VALUES ('cr1','c1')"
        )
        conn.execute(
            """
            INSERT INTO event_normalization_configs
            VALUES ('n1','t1','s1',?, '{}', CURRENT_TIMESTAMP)
            """,
            ("a" * 64,),
        )
        conn.execute(
            """
            INSERT INTO event_normalization_runs(
                normalization_run_id, normalization_version, clustering_run_id,
                started_at, status, as_of, selection_json
            ) VALUES (
                'nr1','n1','cr1','2026-08-24T20:00:00+00:00',
                'running','2026-08-24T20:00:00+00:00','{}'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO normalized_event_identities(
                event_id, identity_method, identity_key
            ) VALUES ('e1','test','event-1')
            """
        )
        conn.execute(
            """
            INSERT INTO normalized_event_versions(
                event_version_id,event_id,event_type,event_scope,
                occurred_at,event_time_status,resolved_status,
                normalized_content_sha256,normalized_event_json,
                parser_or_model_version
            ) VALUES (
                'ev1','e1','earnings','company',
                '2026-08-24T18:00:00+00:00','explicit_occurrence','observed',
                ?, '{}','test-v1'
            )
            """,
            ("b" * 64,),
        )
        conn.execute(
            """
            INSERT INTO normalized_event_observations(
                event_observation_id, normalization_run_id, event_id,
                event_version_id, observation_sequence, observation_kind,
                available_at, evidence_cutoff_at, availability_is_point_in_time
            ) VALUES (
                'eo1','nr1','e1','ev1',1,'initial',
                '2026-08-24T18:07:00+00:00',
                '2026-08-24T18:07:00+00:00',1
            )
            """
        )
        row = conn.execute(
            """
            SELECT v.occurred_at, o.available_at
            FROM normalized_event_versions v
            JOIN normalized_event_observations o
              ON o.event_version_id=v.event_version_id
            """
        ).fetchone()
        assert row[0] < row[1]


def test_semantic_type_is_not_economic_direction(tmp_path):
    db = tmp_path / "db.sqlite"
    make_base(db)
    apply(db)

    with sqlite3.connect(db) as conn:
        cols = {
            r[1]
            for r in conn.execute("PRAGMA table_info(event_evidence_semantics)")
        }
        assert "semantic_type" in cols
        assert "classification_confidence" in cols
        assert "expected_direction" not in cols
        assert "impact" not in cols
        assert "source_reliability" not in cols

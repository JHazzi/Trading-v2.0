import sqlite3
from pathlib import Path

from database.apply_migration_020 import apply

ROOT = Path(__file__).resolve().parents[1]


def test_apply_020_preserves_existing_019(tmp_path):
    db = tmp_path / "x.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        CREATE TABLE schema_migrations(
          version TEXT PRIMARY KEY,
          name TEXT NOT NULL
        );
        INSERT INTO schema_migrations VALUES ('019','event_brain_v001');

        CREATE TABLE assets(
          asset_id INTEGER PRIMARY KEY
        );
        CREATE TABLE entities(
          entity_id INTEGER PRIMARY KEY,
          entity_type TEXT NOT NULL,
          canonical_name TEXT NOT NULL,
          external_id TEXT,
          country TEXT,
          metadata_json TEXT,
          UNIQUE(entity_type,canonical_name)
        );
        CREATE TABLE relation_types(
          relation_type TEXT PRIMARY KEY,
          description TEXT,
          signed INTEGER NOT NULL DEFAULT 0
        );
        """)
        conn.commit()

    assert apply(db) == "applied"

    with sqlite3.connect(db) as conn:
        migrations = dict(
            conn.execute(
                "SELECT version,name FROM schema_migrations"
            ).fetchall()
        )
    assert migrations["019"] == "event_brain_v001"
    assert migrations["020"] == "event_graph_brain_foundation"


def test_apply_020_is_idempotent(tmp_path):
    db = tmp_path / "x.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        CREATE TABLE schema_migrations(
          version TEXT PRIMARY KEY,
          name TEXT NOT NULL
        );
        INSERT INTO schema_migrations VALUES ('019','event_brain_v001');

        CREATE TABLE assets(asset_id INTEGER PRIMARY KEY);
        CREATE TABLE entities(
          entity_id INTEGER PRIMARY KEY,
          entity_type TEXT NOT NULL,
          canonical_name TEXT NOT NULL,
          external_id TEXT,
          country TEXT,
          metadata_json TEXT,
          UNIQUE(entity_type,canonical_name)
        );
        CREATE TABLE relation_types(
          relation_type TEXT PRIMARY KEY,
          description TEXT,
          signed INTEGER NOT NULL DEFAULT 0
        );
        """)
        conn.commit()
    assert apply(db) == "applied"
    assert apply(db) == "already_applied"

import json
import sqlite3
from pathlib import Path

import pytest

import database.apply_migration_011 as migration_011


def _create_assets_db(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE assets (
                asset_id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL UNIQUE
            )
            """
        )
        conn.execute(
            "INSERT INTO assets(asset_id, ticker) VALUES (1, 'AAPL')"
        )


def _create_migration_registry(conn: sqlite3.Connection) -> None:
    conn.execute(
        """
        CREATE TABLE schema_migrations (
            version TEXT PRIMARY KEY,
            name TEXT NOT NULL,
            applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
        )
        """
    )


def _table_names(db: Path) -> set[str]:
    with sqlite3.connect(db) as conn:
        return {
            str(row[0])
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }


def test_missing_database_is_rejected_without_creating_orphan(tmp_path):
    db = tmp_path / "missing.db"

    with pytest.raises(FileNotFoundError, match="DB no existe"):
        migration_011.apply(db)

    assert not db.exists()


def test_missing_assets_prerequisite_does_not_mutate_database(tmp_path):
    db = tmp_path / "missing-assets.db"
    with sqlite3.connect(db) as conn:
        conn.execute("CREATE TABLE sentinel(value TEXT PRIMARY KEY)")
        conn.execute("INSERT INTO sentinel(value) VALUES ('must-survive')")

    before_tables = _table_names(db)
    with pytest.raises(RuntimeError, match="falta assets"):
        migration_011.apply(db)

    assert _table_names(db) == before_tables
    with sqlite3.connect(db) as conn:
        assert conn.execute("SELECT value FROM sentinel").fetchone() == (
            "must-survive",
        )


def test_incompatible_migration_registry_is_rejected_without_mutation(
    tmp_path,
):
    db = tmp_path / "bad-registry.db"
    _create_assets_db(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO schema_migrations(version, name)
            VALUES ('010', 'legacy-marker')
            """
        )

    before_tables = _table_names(db)
    with pytest.raises(RuntimeError, match="schema_migrations"):
        migration_011.apply(db)

    assert _table_names(db) == before_tables
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT version, name FROM schema_migrations"
        ).fetchall() == [("010", "legacy-marker")]


def test_malformed_preexisting_target_is_rejected_without_mutation(
    tmp_path,
):
    db = tmp_path / "malformed-target.db"
    _create_assets_db(db)
    with sqlite3.connect(db) as conn:
        _create_migration_registry(conn)
        conn.execute(
            """
            CREATE TABLE ingestion_sources (
                source_id TEXT PRIMARY KEY,
                legacy_note TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO ingestion_sources(source_id, legacy_note)
            VALUES ('legacy', 'must-survive')
            """
        )

    before_tables = _table_names(db)
    with pytest.raises(
        RuntimeError,
        match="Contrato preexistente incompleto",
    ):
        migration_011.apply(db)

    assert _table_names(db) == before_tables
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT source_id, legacy_note FROM ingestion_sources"
        ).fetchall() == [("legacy", "must-survive")]
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = '011'"
        ).fetchone() is None


def test_version_collision_is_rejected_without_mutation(tmp_path):
    db = tmp_path / "collision.db"
    _create_assets_db(db)
    with sqlite3.connect(db) as conn:
        _create_migration_registry(conn)
        conn.execute(
            """
            INSERT INTO schema_migrations(version, name)
            VALUES ('011', 'different_migration')
            """
        )

    before_tables = _table_names(db)
    with pytest.raises(RuntimeError, match="Colisión"):
        migration_011.apply(db)

    assert _table_names(db) == before_tables
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT name FROM schema_migrations WHERE version = '011'"
        ).fetchone() == ("different_migration",)
        assert "ingestion_sources" not in _table_names(db)


def test_invalid_sql_rolls_back_all_ddl_and_dml(tmp_path, monkeypatch):
    db = tmp_path / "atomic.db"
    _create_assets_db(db)
    invalid_migration = tmp_path / "invalid_011.sql"
    invalid_migration.write_text(
        migration_011.MIGRATION.read_text(encoding="utf-8")
        + "\nCREATE TABLE atomic_probe(value TEXT);\n"
        + "THIS IS NOT VALID SQL;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(
        migration_011,
        "MIGRATION",
        invalid_migration,
    )

    with pytest.raises(sqlite3.Error):
        migration_011.apply(db)

    assert _table_names(db) == {"assets"}


def test_rerun_is_idempotent_and_preserves_unrelated_sources(tmp_path):
    db = tmp_path / "rerun.db"
    _create_assets_db(db)

    first = migration_011.apply(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO ingestion_sources(
                source_id,
                source_name,
                source_type,
                access_method
            )
            VALUES ('fixture_source', 'Fixture', 'test', 'fixture')
            """
        )
    second = migration_011.apply(db)

    assert first["status"] == "applied"
    assert second["status"] == "applied"
    assert {
        "schema_migrations",
        *migration_011.REQUIRED,
    } <= _table_names(db)

    with sqlite3.connect(db) as conn:
        migration_rows = conn.execute(
            """
            SELECT version, name
            FROM schema_migrations
            WHERE version = '011'
            """
        ).fetchall()
        sec_source = conn.execute(
            """
            SELECT
                source_name,
                source_type,
                base_url,
                access_method,
                rate_limit_per_second,
                metadata_json
            FROM ingestion_sources
            WHERE source_id = 'sec_edgar'
            """
        ).fetchone()
        source_ids = {
            str(row[0])
            for row in conn.execute(
                "SELECT source_id FROM ingestion_sources"
            )
        }
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()

    assert migration_rows == [("011", "source_document_foundation")]
    assert sec_source[:5] == (
        "SEC EDGAR",
        "regulator",
        "https://data.sec.gov",
        "rest_and_bulk",
        5.0,
    )
    assert json.loads(sec_source[5])[
        "reliability_is_not_hardcoded"
    ] is True
    assert source_ids == {"fixture_source", "sec_edgar"}
    assert fk_errors == []


def test_rerun_accepts_valid_legacy_sec_filing_files_contract(tmp_path):
    db = tmp_path / "legacy-rerun.db"
    _create_assets_db(db)
    migration_011.apply(db)

    with sqlite3.connect(db) as conn:
        conn.execute("DROP TABLE sec_filing_files")
        conn.execute(
            """
            CREATE TABLE sec_filing_files (
                filing_raw_document_id TEXT NOT NULL,
                sequence_number TEXT NOT NULL,
                document_name TEXT NOT NULL,
                document_type TEXT,
                description TEXT,
                source_url TEXT NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 0
                    CHECK (is_primary IN (0, 1)),
                raw_document_id TEXT,
                metadata_json TEXT,
                PRIMARY KEY(
                    filing_raw_document_id,
                    sequence_number,
                    document_name
                ),
                FOREIGN KEY(filing_raw_document_id)
                    REFERENCES sec_filings(raw_document_id)
                    ON DELETE CASCADE,
                FOREIGN KEY(raw_document_id)
                    REFERENCES raw_source_documents(raw_document_id)
                    ON DELETE SET NULL
            )
            """
        )

    result = migration_011.apply(db)

    assert result["status"] == "applied"
    with sqlite3.connect(db) as conn:
        columns = {
            str(row[1])
            for row in conn.execute(
                "PRAGMA table_info(sec_filing_files)"
            )
        }
        migration_row = conn.execute(
            "SELECT name FROM schema_migrations WHERE version = '011'"
        ).fetchone()
    assert "inventory_status" not in columns
    assert migration_row == ("source_document_foundation",)


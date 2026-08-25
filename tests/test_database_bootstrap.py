import sqlite3
from pathlib import Path

import pytest

from database.apply_migration_012 import apply as apply_migration_012
from database.apply_migration_013 import apply as apply_migration_013
from database.apply_migration_014 import apply as apply_migration_014
from database.apply_migration_015 import apply as apply_migration_015
from database.apply_migration_016 import apply as apply_migration_016
from database.init_db import (
    CANONICAL_MIGRATIONS,
    REQUIRED_CURRENT_TABLES,
    initialize_database,
)


ROOT = Path(__file__).resolve().parents[1]


def table_columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        row[1]
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def test_fresh_bootstrap_applies_only_canonical_migrations(tmp_path):
    db = tmp_path / "fresh.db"
    expected_versions = tuple(
        version for version, _ in CANONICAL_MIGRATIONS
    )
    canonical_filenames = tuple(
        filename for _, filename in CANONICAL_MIGRATIONS
    )
    expected_names = {
        version: Path(filename).stem.removeprefix(f"{version}_")
        for version, filename in CANONICAL_MIGRATIONS
    }
    assert "009_event_layer.sql" not in canonical_filenames

    applied = initialize_database(
        ROOT / "database" / "schema.sql",
        db,
        ROOT / "database" / "migrations",
    )

    assert applied == expected_versions
    assert db.is_file()

    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        versions = tuple(
            row[0]
            for row in conn.execute(
                "SELECT version FROM schema_migrations ORDER BY version"
            )
        )
        migration_names = dict(
            conn.execute(
                "SELECT version, name FROM schema_migrations"
            )
        )
        sources = dict(
            conn.execute(
                """
                SELECT source_id, source_name
                FROM ingestion_sources
                WHERE source_id IN ('sec_edgar', 'yahoo_finance')
                """
            )
        )

        assert REQUIRED_CURRENT_TABLES <= tables
        assert versions == expected_versions
        assert migration_names == expected_names
        assert migration_names["009"] == "asset_universe_membership"
        assert migration_names["012"] == "sec_filing_documents"
        assert migration_names["013"] == "daily_price_observation_foundation"
        assert migration_names["014"] == "sec_filing_observations"
        assert migration_names["015"] == "deterministic_event_clustering"
        assert migration_names["016"] == "sec_filing_metadata_versioning"
        assert "session_id" in table_columns(conn, "price_bars")
        assert "trading_day" in table_columns(conn, "price_bars")
        assert "observed_bars" in table_columns(
            conn, "realized_outcomes"
        )
        assert "return_percentile_390m" in table_columns(
            conn, "market_state_v002_snapshots"
        )
        assert "worker_name" in table_columns(conn, "ingestion_runs")
        assert "source_id" in table_columns(
            conn, "source_ingestion_runs"
        )
        inventory_status = conn.execute(
            """
            SELECT type, "notnull", dflt_value
            FROM pragma_table_info('sec_filing_files')
            WHERE name = 'inventory_status'
            """
        ).fetchone()
        assert inventory_status == ("TEXT", 1, "'current'")
        attempt_run = conn.execute(
            """
            SELECT type, "notnull", dflt_value
            FROM pragma_table_info('sec_filing_files')
            WHERE name = 'last_attempt_run_id'
            """
        ).fetchone()
        assert attempt_run == ("TEXT", 0, None)
        attempt_run_fk = conn.execute(
            """
            SELECT "table" FROM pragma_foreign_key_list('sec_filing_files')
            WHERE "from" = 'last_attempt_run_id'
            """
        ).fetchone()
        assert attempt_run_fk == ("source_ingestion_runs",)
        assert sources == {
            "sec_edgar": "SEC EDGAR",
            "yahoo_finance": "Yahoo Finance via yfinance",
        }
        assert conn.execute(
            "SELECT COUNT(*) FROM price_bars"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM price_bar_observations"
        ).fetchone()[0] == 0

    with pytest.raises(FileExistsError):
        initialize_database(
            ROOT / "database" / "schema.sql",
            db,
            ROOT / "database" / "migrations",
        )


def test_current_migration_appliers_are_idempotent_after_bootstrap(tmp_path):
    db = tmp_path / "fresh-for-reapply.db"
    initialize_database(
        ROOT / "database" / "schema.sql",
        db,
        ROOT / "database" / "migrations",
    )

    appliers = (
        apply_migration_012,
        apply_migration_013,
        apply_migration_014,
        apply_migration_015,
        apply_migration_016,
    )
    for _ in range(2):
        for apply_migration in appliers:
            result = apply_migration(db)
            assert result["status"] == "applied"

    with sqlite3.connect(db) as conn:
        current_names = dict(
            conn.execute(
                """
                SELECT version, name
                FROM schema_migrations
                WHERE version BETWEEN '012' AND '016'
                """
            )
        )
    assert current_names == {
        "012": "sec_filing_documents",
        "013": "daily_price_observation_foundation",
        "014": "sec_filing_observations",
        "015": "deterministic_event_clustering",
        "016": "sec_filing_metadata_versioning",
    }

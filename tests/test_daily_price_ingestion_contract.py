import gzip
import hashlib
import json
import sqlite3
from datetime import date, datetime, time, timezone
from types import SimpleNamespace

import pandas as pd
import pytest

import database.apply_migration_013 as migration_013
from database.apply_migration_011 import apply as apply_011
from database.apply_migration_013 import apply as apply_013
from ingestion.prices.yahoo_daily_v1 import (
    BATCH_VERSION,
    PARSER_VERSION,
    PROVIDER_TIMEOUT_SECONDS,
    QUALITY_VERSION,
    RawPriceStore,
    SessionBounds,
    canonical_provider_payload,
    fetch_yahoo_daily,
    persist_provider_frame,
    resolve_asset,
    run_pilot,
    validate_pilot_window,
    validate_raw_root,
)


class FixtureSessions:
    exchange = "XNAS"
    calendar_name = "XNAS"

    def bounds(self, trading_day):
        day = datetime.fromisoformat(trading_day).date()
        if day.weekday() >= 5 or day == date(2026, 1, 1):
            return None
        return SessionBounds(
            exchange=self.exchange,
            calendar_name=self.calendar_name,
            trading_day=trading_day,
            open_utc=datetime.combine(
                day, time(14, 30), tzinfo=timezone.utc
            ),
            close_utc=datetime.combine(
                day, time(21, 0), tzinfo=timezone.utc
            ),
        )


def make_db(tmp_path, *, exchange="XNAS"):
    db = tmp_path / "market.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE assets (
                asset_id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL UNIQUE,
                exchange TEXT,
                currency TEXT,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            INSERT INTO assets(asset_id, ticker, exchange, currency)
            VALUES (1, 'AAPL', ?, 'USD')
            """,
            (exchange,),
        )
        conn.execute(
            """
            CREATE TABLE price_bars (
                price_bar_id INTEGER PRIMARY KEY,
                asset_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                interval TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                source TEXT NOT NULL
            )
            """
        )
        conn.execute(
            """
            INSERT INTO price_bars(
                price_bar_id, asset_id, timestamp, interval,
                open, high, low, close, source
            )
            VALUES (7, 1, '2026-01-01T14:30:00+00:00', '1m',
                    10, 11, 9, 10.5, 'legacy')
            """
        )
    apply_011(db)
    return db


def fixture_frame(close_second=102.0):
    index = pd.DatetimeIndex(
        [
            "2026-01-02 00:00:00-05:00",
            "2026-01-05 00:00:00-05:00",
        ],
        name="Date",
    )
    return pd.DataFrame(
        {
            "Open": [100.0, 101.0],
            "High": [103.0, 104.0],
            "Low": [99.0, 100.0],
            "Close": [101.5, close_second],
            "Adj Close": [100.5, close_second - 1.0],
            "Volume": [1_000_000, 1_200_000],
            "Dividends": [0.25, 0.0],
            "Stock Splits": [0.0, 4.0],
            "Capital Gains": [0.0, 0.0],
        },
        index=index,
    )


def test_migration_is_additive_and_registers_provider_source(tmp_path):
    db = make_db(tmp_path)

    first = apply_013(db)
    second = apply_013(db)

    with sqlite3.connect(db) as conn:
        legacy = conn.execute(
            "SELECT price_bar_id, close, source FROM price_bars"
        ).fetchone()
        source = conn.execute(
            """
            SELECT access_method, metadata_json
            FROM ingestion_sources
            WHERE source_id = 'yahoo_finance'
            """
        ).fetchone()
        migration_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM schema_migrations
            WHERE version = '013'
            """
        ).fetchone()[0]
        new_tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }

    assert first["status"] == second["status"] == "applied"
    assert first["legacy_price_bars_modified"] is False
    assert legacy == (7, 10.5, "legacy")
    assert source[0] == "python_provider_library"
    metadata = json.loads(source[1])
    assert metadata["exact_http_bytes_preserved"] is False
    assert metadata["point_in_time_history"] is False
    assert migration_count == 1
    assert {
        "raw_price_batch_retrievals",
        "price_bar_versions",
        "price_bar_observations",
        "corporate_action_versions",
        "corporate_action_observations",
    } <= new_tables



def test_same_output_is_idempotent_and_causally_available(tmp_path):
    db = make_db(tmp_path)
    apply_013(db)
    store = RawPriceStore(tmp_path / "raw")

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        first = persist_provider_frame(
            conn,
            store,
            asset_id=1,
            symbol="AAPL",
            exchange="XNAS",
            requested_start="2026-01-01",
            requested_end="2026-01-06",
            retrieved_at="2026-08-24T12:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=fixture_frame(),
            session_resolver=FixtureSessions(),
        )
        second = persist_provider_frame(
            conn,
            store,
            asset_id=1,
            symbol="AAPL",
            exchange="XNAS",
            requested_start="2026-01-01",
            requested_end="2026-01-06",
            retrieved_at="2026-08-25T12:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=fixture_frame(),
            session_resolver=FixtureSessions(),
        )
        conn.commit()

        batch = conn.execute(
            """
            SELECT lineage_kind, is_exact_http_response, raw_sha256,
                   storage_path, retrieved_at, batch_version,
                   parser_version
            FROM raw_price_batches
            """
        ).fetchone()
        bars = conn.execute(
            """
            SELECT version.trading_day, version.bar_end_utc,
                   observation.available_at, observation.observed_at,
                   version.close, version.adjusted_close,
                   observation.observation_kind,
                   observation.state_revision_number,
                   observation.point_in_time_verified
            FROM price_bar_observations AS observation
            JOIN price_bar_versions AS version
              USING (price_bar_version_id)
            ORDER BY version.trading_day,
                     observation.observation_sequence
            """
        ).fetchall()
        actions = conn.execute(
            """
            SELECT version.action_type, version.raw_value,
                   observation.announcement_available_at,
                   observation.available_at,
                   observation.observed_at,
                   observation.observation_kind,
                   version.is_present
            FROM corporate_action_observations AS observation
            JOIN corporate_action_versions AS version
              USING (corporate_action_version_id)
            ORDER BY version.action_type,
                     observation.observation_sequence
            """
        ).fetchall()
        retrieval_count = conn.execute(
            "SELECT COUNT(*) FROM raw_price_batch_retrievals"
        ).fetchone()[0]
        bar_version_count = conn.execute(
            "SELECT COUNT(*) FROM price_bar_versions"
        ).fetchone()[0]
        action_version_count = conn.execute(
            "SELECT COUNT(*) FROM corporate_action_versions"
        ).fetchone()[0]
        identifier_count = conn.execute(
            "SELECT COUNT(*) FROM asset_identifier_history"
        ).fetchone()[0]
        quality = conn.execute(
            """
            SELECT q.quality_version, COUNT(r.quality_result_id),
                   SUM(
                       CASE WHEN r.check_status = 'fail' THEN 1 ELSE 0 END
                   )
            FROM price_quality_runs q
            JOIN price_quality_results r USING (quality_run_id)
            GROUP BY q.quality_version
            """
        ).fetchone()
        legacy_count = conn.execute(
            "SELECT COUNT(*) FROM price_bars"
        ).fetchone()[0]

    assert first.batch_inserted is True
    assert second.batch_inserted is False
    assert first.raw_batch_id == second.raw_batch_id
    assert first.batch_retrieval_inserted is True
    assert second.batch_retrieval_inserted is True
    assert first.batch_retrieval_id != second.batch_retrieval_id
    assert first.bars_inserted == 2
    assert second.bars_inserted == 0
    assert first.bar_observations_inserted == 2
    assert second.bar_observations_inserted == 2
    assert first.actions_inserted == 2
    assert second.actions_inserted == 0
    assert first.action_observations_inserted == 2
    assert second.action_observations_inserted == 2
    assert retrieval_count == 2
    assert bar_version_count == 2
    assert action_version_count == 2
    assert batch[0:2] == ("provider_library_output", 0)
    assert batch[4] == "2026-08-24T12:00:00+00:00"
    assert batch[5:] == (BATCH_VERSION, PARSER_VERSION)
    assert len(bars) == 4
    initial_bars = bars[::2]
    repeated_bars = bars[1::2]
    assert all(row[2] == row[1] for row in initial_bars)
    assert all(row[3] > row[2] for row in initial_bars)
    assert all(row[6:] == ("initial_backfill", 1, 0) for row in initial_bars)
    assert all(row[2] == row[3] for row in repeated_bars)
    assert all(row[6:] == ("unchanged", 1, 0) for row in repeated_bars)
    assert bars[0][4:6] == (101.5, 100.5)
    assert [(row[0], row[1]) for row in actions[::2]] == [
        ("dividend", 0.25),
        ("stock_split", 4.0),
    ]
    assert all(row[2] is None for row in actions)
    assert all(row[3] == row[4] for row in actions)
    assert [row[5] for row in actions] == [
        "initial_observation", "unchanged",
        "initial_observation", "unchanged",
    ]
    assert identifier_count == 1
    assert quality == (QUALITY_VERSION, 20, 0)
    assert legacy_count == 1

    with gzip.open(batch[3], "rb") as stream:
        stored = stream.read()
    assert hashlib.sha256(stored).hexdigest() == batch[2]
    payload = json.loads(stored)
    assert payload["lineage_kind"] == "provider_library_output"
    assert payload["is_exact_http_response"] is False
    assert payload["request"]["auto_adjust"] is False
    assert payload["request"]["actions"] is True
    assert payload["request"]["timeout_seconds"] == PROVIDER_TIMEOUT_SECONDS
    assert payload["request"]["exceptions_visible"] is True
    assert "raise_errors" not in payload["request"]


def test_changed_value_creates_append_only_revision(tmp_path):
    db = make_db(tmp_path)
    apply_013(db)

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        first = persist_provider_frame(
            conn,
            RawPriceStore(tmp_path / "raw"),
            asset_id=1,
            symbol="AAPL",
            exchange="XNAS",
            requested_start="2026-01-01",
            requested_end="2026-01-06",
            retrieved_at="2026-08-24T12:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=fixture_frame(close_second=102.0),
            session_resolver=FixtureSessions(),
        )
        revised = persist_provider_frame(
            conn,
            RawPriceStore(tmp_path / "raw"),
            asset_id=1,
            symbol="AAPL",
            exchange="XNAS",
            requested_start="2026-01-01",
            requested_end="2026-01-06",
            retrieved_at="2026-08-25T12:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=fixture_frame(close_second=102.75),
            session_resolver=FixtureSessions(),
        )
        conn.commit()
        batch_count = conn.execute(
            "SELECT COUNT(*) FROM raw_price_batches"
        ).fetchone()[0]
        observations = conn.execute(
            """
            SELECT version.close, observation.observed_at,
                   observation.available_at,
                   observation.observation_kind,
                   observation.state_revision_number
            FROM price_bar_observations AS observation
            JOIN price_bar_versions AS version
              USING (price_bar_version_id)
            WHERE observation.trading_day = '2026-01-05'
            ORDER BY observation.observation_sequence
            """
        ).fetchall()
        unchanged_day_versions = conn.execute(
            "SELECT COUNT(*) FROM price_bar_versions WHERE trading_day = '2026-01-02'"
        ).fetchone()[0]


    assert revised.batch_inserted is True
    assert first.raw_batch_id != revised.raw_batch_id
    assert first.raw_sha256 != revised.raw_sha256
    assert batch_count == 2
    assert unchanged_day_versions == 1
    assert observations == [
        (102.0, "2026-08-24T12:00:00+00:00", "2026-01-05T21:00:00+00:00", "initial_backfill", 1),
        (102.75, "2026-08-25T12:00:00+00:00", "2026-08-25T12:00:00+00:00", "revision", 2),
    ]


def test_provider_call_is_unadjusted_and_keeps_actions():
    captured = {}

    class FakeTicker:
        def history(self, **kwargs):
            captured.update(kwargs)
            return fixture_frame()

    frame, version = fetch_yahoo_daily(
        "AAPL",
        "2026-01-01",
        "2026-01-06",
        ticker_factory=lambda symbol: FakeTicker(),
        provider_library_version="fixture-1.0",
    )

    assert len(frame) == 2
    assert version == "fixture-1.0"
    assert captured == {
        "start": "2026-01-01",
        "end": "2026-01-06",
        "interval": "1d",
        "auto_adjust": False,
        "actions": True,
        "repair": False,
        "keepna": True,
        "timeout": PROVIDER_TIMEOUT_SECONDS,
        "raise_errors": True,
    }


def test_modern_provider_config_avoids_deprecated_raise_errors():
    captured = {}

    class FakeTicker:
        def history(self, **kwargs):
            captured.update(kwargs)
            return fixture_frame()

    debug = SimpleNamespace(hide_exceptions=True)
    provider = SimpleNamespace(
        __version__="fixture-1.6",
        Ticker=lambda symbol: FakeTicker(),
        config=SimpleNamespace(debug=debug),
    )
    frame, version = fetch_yahoo_daily(
        "AAPL",
        "2026-01-01",
        "2026-01-06",
        provider_module=provider,
    )

    assert len(frame) == 2
    assert version == "fixture-1.6"
    assert debug.hide_exceptions is False
    assert "raise_errors" not in captured
    assert captured["auto_adjust"] is False
    assert captured["actions"] is True


def test_exchange_and_pilot_limits_are_explicit(tmp_path):
    db = make_db(tmp_path, exchange=None)
    apply_013(db)

    with sqlite3.connect(db) as conn:
        with pytest.raises(ValueError, match="--exchange"):
            resolve_asset(conn, "AAPL", None)
        assert resolve_asset(conn, "AAPL", "XNAS") == (
            1,
            "AAPL",
            "XNAS",
        )
        assert resolve_asset(conn, "AAPL", "NASDAQ") == (
            1,
            "AAPL",
            "XNAS",
        )

    validate_pilot_window("2026-01-01", "2026-01-31", 30)
    with pytest.raises(ValueError, match="excede"):
        validate_pilot_window("2026-01-01", "2026-02-01", 30)
    with pytest.raises(ValueError, match="posterior"):
        validate_pilot_window("2026-01-01", "2026-01-01", 30)

    raw_root = tmp_path / "data" / "raw"
    assert validate_raw_root(raw_root) == raw_root
    with pytest.raises(ValueError, match="raíz data/raw"):
        validate_raw_root(raw_root / "prices")

def test_pilot_links_batch_to_source_ingestion_run(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    apply_013(db)

    monkeypatch.setattr(
        "ingestion.prices.yahoo_daily_v1.fetch_yahoo_daily",
        lambda symbol, start, end: (fixture_frame(), "fixture-1.0"),
    )
    result = run_pilot(
        db=db,
        raw_root=tmp_path / "raw",
        ticker="AAPL",
        requested_start="2026-01-01",
        requested_end="2026-01-06",
        exchange_override=None,
        max_days=10,
    )
    repeated = run_pilot(
        db=db,
        raw_root=tmp_path / "raw",
        ticker="AAPL",
        requested_start="2026-01-01",
        requested_end="2026-01-06",
        exchange_override=None,
        max_days=10,
    )

    with sqlite3.connect(db) as conn:
        run = conn.execute(
            """
            SELECT status, documents_discovered, documents_inserted,
                   documents_existing, error_count
            FROM source_ingestion_runs
            WHERE run_id = ?
            """,
            (result["run_id"],),
        ).fetchone()
        repeated_run = conn.execute(
            """
            SELECT status, documents_discovered, documents_inserted,
                   documents_existing, error_count
            FROM source_ingestion_runs
            WHERE run_id = ?
            """,
            (repeated["run_id"],),
        ).fetchone()
        batch_run_id = conn.execute(
            """
            SELECT source_run_id
            FROM raw_price_batches
            WHERE raw_batch_id = ?
            """,
            (result["raw_batch_id"],),
        ).fetchone()[0]
        retrieval_run_ids = {
            row[0]
            for row in conn.execute(
                "SELECT source_run_id FROM raw_price_batch_retrievals"
            )
        }
        batch_count = conn.execute(
            "SELECT COUNT(*) FROM raw_price_batches"
        ).fetchone()[0]
        violations = conn.execute(
            "PRAGMA foreign_key_check"
        ).fetchall()

    assert run == ("completed", 1, 1, 0, 0)
    assert repeated_run == ("completed", 1, 0, 1, 0)
    assert batch_run_id == result["run_id"]
    assert result["raw_batch_id"] == repeated["raw_batch_id"]
    assert retrieval_run_ids == {
        result["run_id"], repeated["run_id"]
    }
    assert batch_count == 1
    assert violations == []


def test_overlapping_windows_do_not_create_false_versions(tmp_path):
    db = make_db(tmp_path)
    apply_013(db)
    store = RawPriceStore(tmp_path / "raw")

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        persist_provider_frame(
            conn,
            store,
            asset_id=1,
            symbol="AAPL",
            exchange="XNAS",
            requested_start="2026-01-01",
            requested_end="2026-01-06",
            retrieved_at="2026-08-24T12:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=fixture_frame(),
            session_resolver=FixtureSessions(),
        )
        overlap = persist_provider_frame(
            conn,
            store,
            asset_id=1,
            symbol="AAPL",
            exchange="XNAS",
            requested_start="2026-01-05",
            requested_end="2026-01-06",
            retrieved_at="2026-08-25T12:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=fixture_frame().iloc[[1]],
            session_resolver=FixtureSessions(),
        )
        conn.commit()
        version_count = conn.execute(
            "SELECT COUNT(*) FROM price_bar_versions"
        ).fetchone()[0]
        day_observations = conn.execute(
            """
            SELECT observation_kind, state_revision_number
            FROM price_bar_observations
            WHERE trading_day = '2026-01-05'
            ORDER BY observation_sequence
            """
        ).fetchall()

    assert overlap.batch_inserted is True
    assert overlap.bars_inserted == 0
    assert overlap.bar_observations_inserted == 1
    assert version_count == 2
    assert day_observations == [("initial_backfill", 1), ("unchanged", 1)]


def test_a_to_b_to_a_is_an_auditable_reversion(tmp_path):
    db = make_db(tmp_path)
    apply_013(db)
    store = RawPriceStore(tmp_path / "raw")

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        results = []
        for retrieved_at, close in (
            ("2026-08-24T12:00:00+00:00", 102.0),
            ("2026-08-25T12:00:00+00:00", 102.75),
            ("2026-08-26T12:00:00+00:00", 102.0),
        ):
            results.append(
                persist_provider_frame(
                    conn,
                    store,
                    asset_id=1,
                    symbol="AAPL",
                    exchange="XNAS",
                    requested_start="2026-01-01",
                    requested_end="2026-01-06",
                    retrieved_at=retrieved_at,
                    provider_library_version="fixture-1.0",
                    frame=fixture_frame(close_second=close),
                    session_resolver=FixtureSessions(),
                )
            )
        conn.commit()
        timeline = conn.execute(
            """
            SELECT price_bar_version_id, observation_kind,
                   state_revision_number, available_at
            FROM price_bar_observations
            WHERE trading_day = '2026-01-05'
            ORDER BY observation_sequence
            """
        ).fetchall()
        batch_count = conn.execute(
            "SELECT COUNT(*) FROM raw_price_batches"
        ).fetchone()[0]
        retrieval_count = conn.execute(
            "SELECT COUNT(*) FROM raw_price_batch_retrievals"
        ).fetchone()[0]

    assert results[0].raw_batch_id == results[2].raw_batch_id
    assert results[0].raw_batch_id != results[1].raw_batch_id
    assert batch_count == 2
    assert retrieval_count == 3
    assert timeline[0][0] == timeline[2][0]
    assert timeline[0][0] != timeline[1][0]
    assert [row[1] for row in timeline] == [
        "initial_backfill", "revision", "reversion"
    ]
    assert [row[2] for row in timeline] == [1, 2, 3]
    assert timeline[0][3] == "2026-01-05T21:00:00+00:00"
    assert timeline[1][3] == "2026-08-25T12:00:00+00:00"
    assert timeline[2][3] == "2026-08-26T12:00:00+00:00"


def test_open_session_is_raw_only_and_fails_quality(tmp_path):
    db = make_db(tmp_path)
    apply_013(db)

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        result = persist_provider_frame(
            conn,
            RawPriceStore(tmp_path / "raw"),
            asset_id=1,
            symbol="AAPL",
            exchange="XNAS",
            requested_start="2026-01-05",
            requested_end="2026-01-06",
            retrieved_at="2026-01-05T18:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=fixture_frame().iloc[[1]],
            session_resolver=FixtureSessions(),
        )
        conn.commit()
        version_count = conn.execute(
            "SELECT COUNT(*) FROM price_bar_versions"
        ).fetchone()[0]
        action_count = conn.execute(
            "SELECT COUNT(*) FROM corporate_action_versions"
        ).fetchone()[0]
        quality = conn.execute(
            """
            SELECT check_status, observed_value
            FROM price_quality_results
            WHERE check_name = 'incomplete_session_rows'
            """
        ).fetchone()

    assert result.bars_discovered == 0
    assert result.bars_incomplete_session == 1
    assert version_count == 0
    assert action_count == 0
    assert quality == ("fail", 1.0)


def test_frame_contract_handles_single_ticker_multiindex_and_time(tmp_path):
    frame = fixture_frame()
    frame.columns = pd.MultiIndex.from_tuples(
        [(column, "AAPL") for column in frame.columns],
        names=["Price", "Ticker"],
    )
    payload, rows = canonical_provider_payload(
        frame,
        symbol="AAPL",
        requested_start="2026-01-01",
        requested_end="2026-01-06",
        provider_library_version="fixture-1.0",
        exchange="XNAS",
        calendar_name="XNAS",
    )
    decoded = json.loads(payload)
    assert rows[1]["values"]["Close"] == 102.0
    assert decoded["frame"]["column_schema"]["normalized_columns"][0] == "Open"

    naive = fixture_frame()
    naive.index = naive.index.tz_localize(None)
    with pytest.raises(ValueError, match="zona horaria"):
        canonical_provider_payload(
            naive,
            symbol="AAPL",
            requested_start="2026-01-01",
            requested_end="2026-01-06",
            provider_library_version="fixture-1.0",
            exchange="XNAS",
            calendar_name="XNAS",
        )

    db = make_db(tmp_path)
    apply_013(db)
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(ValueError, match="fuera de"):
            persist_provider_frame(
                conn,
                RawPriceStore(tmp_path / "raw"),
                asset_id=1,
                symbol="AAPL",
                exchange="XNAS",
                requested_start="2026-01-01",
                requested_end="2026-01-05",
                retrieved_at="2026-08-24T12:00:00+00:00",
                provider_library_version="fixture-1.0",
                frame=fixture_frame(),
                session_resolver=FixtureSessions(),
            )


def test_existing_raw_file_is_verified(tmp_path):
    store = RawPriceStore(tmp_path / "raw")
    path, _, _ = store.write("AAPL", b"first payload")
    path.write_bytes(b"not a gzip stream")

    with pytest.raises(RuntimeError, match="corrupto"):
        store.write("AAPL", b"first payload")

def test_migration_identity_and_contract_failures_are_atomic(tmp_path):
    db = make_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO schema_migrations(version, name)
            VALUES ('013', 'wrong_migration')
            """
        )

    with pytest.raises(RuntimeError, match="Colisión"):
        apply_013(db)

    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "raw_price_batches" not in tables
        conn.execute(
            "DELETE FROM schema_migrations WHERE version = '013'"
        )
        conn.execute(
            """
            CREATE TABLE price_quality_runs (
                quality_run_id TEXT PRIMARY KEY
            )
            """
        )

    with pytest.raises(RuntimeError, match="Contrato incompleto"):
        apply_013(db)

    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "price_quality_runs" in tables
        assert "raw_price_batches" not in tables
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = '013'"
        ).fetchone() is None
        assert conn.execute(
            """
            SELECT 1 FROM ingestion_sources
            WHERE source_id = 'yahoo_finance'
            """
        ).fetchone() is None



def test_migration_validation_failure_rolls_back_all_ddl(
    tmp_path,
    monkeypatch,
):
    db = make_db(tmp_path)

    def reject_contract(conn):
        raise RuntimeError("forced contract rejection")

    monkeypatch.setattr(
        migration_013,
        "_validate_contract",
        reject_contract,
    )
    with pytest.raises(RuntimeError, match="forced contract rejection"):
        migration_013.apply(db)

    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert "raw_price_batches" not in tables
        assert conn.execute(
            "SELECT 1 FROM schema_migrations WHERE version = '013'"
        ).fetchone() is None
        assert conn.execute(
            """
            SELECT 1 FROM ingestion_sources
            WHERE source_id = 'yahoo_finance'
            """
        ).fetchone() is None


def test_missing_expected_session_is_a_failing_quality_result(tmp_path):
    db = make_db(tmp_path)
    apply_013(db)

    with sqlite3.connect(db) as conn:
        result = persist_provider_frame(
            conn,
            RawPriceStore(tmp_path / "raw"),
            asset_id=1,
            symbol="AAPL",
            exchange="XNAS",
            requested_start="2026-01-01",
            requested_end="2026-01-06",
            retrieved_at="2026-08-24T12:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=fixture_frame().iloc[[1]],
            session_resolver=FixtureSessions(),
        )
        quality = conn.execute(
            """
            SELECT check_status, observed_value, details_json
            FROM price_quality_results
            WHERE quality_run_id = ?
              AND check_name = 'missing_expected_sessions'
            """,
            (result.quality_run_id,),
        ).fetchone()

    assert quality[0:2] == ("fail", 1.0)
    details = json.loads(quality[2])
    assert details["missing_trading_days"] == ["2026-01-02"]
    assert details["expected_closed_session_count"] == 2


def test_quality_is_scoped_to_each_retrieval(tmp_path):
    db = make_db(tmp_path)
    apply_013(db)
    store = RawPriceStore(tmp_path / "raw")
    frame = fixture_frame().iloc[[1]]

    with sqlite3.connect(db) as conn:
        before_close = persist_provider_frame(
            conn,
            store,
            asset_id=1,
            symbol="AAPL",
            exchange="XNAS",
            requested_start="2026-01-05",
            requested_end="2026-01-06",
            retrieved_at="2026-01-05T18:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=frame,
            session_resolver=FixtureSessions(),
        )
        after_close = persist_provider_frame(
            conn,
            store,
            asset_id=1,
            symbol="AAPL",
            exchange="XNAS",
            requested_start="2026-01-05",
            requested_end="2026-01-06",
            retrieved_at="2026-01-05T22:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=frame,
            session_resolver=FixtureSessions(),
        )
        timeline = conn.execute(
            """
            SELECT retrieval.retrieved_at, result.check_status,
                   result.observed_value
            FROM price_quality_runs AS quality
            JOIN raw_price_batch_retrievals AS retrieval
              USING (batch_retrieval_id)
            JOIN price_quality_results AS result
              USING (quality_run_id)
            WHERE result.check_name = 'incomplete_session_rows'
            ORDER BY retrieval.retrieved_at
            """
        ).fetchall()
        bar_count = conn.execute(
            "SELECT COUNT(*) FROM price_bar_versions"
        ).fetchone()[0]

    assert before_close.raw_batch_id == after_close.raw_batch_id
    assert before_close.quality_run_id != after_close.quality_run_id
    assert timeline == [
        ("2026-01-05T18:00:00+00:00", "fail", 1.0),
        ("2026-01-05T22:00:00+00:00", "pass", 0.0),
    ]
    assert bar_count == 1


def test_action_revisions_retraction_reversion_and_missing_columns(tmp_path):
    db = make_db(tmp_path)
    apply_013(db)
    store = RawPriceStore(tmp_path / "raw")

    def action_frame(value):
        frame = fixture_frame().iloc[[0]].copy()
        frame.loc[:, "Dividends"] = value
        return frame

    with sqlite3.connect(db) as conn:
        for retrieved_at, value in (
            ("2026-08-24T12:00:00+00:00", 0.25),
            ("2026-08-25T12:00:00+00:00", 0.30),
            ("2026-08-26T12:00:00+00:00", 0.0),
            ("2026-08-27T12:00:00+00:00", 0.25),
        ):
            persist_provider_frame(
                conn,
                store,
                asset_id=1,
                symbol="AAPL",
                exchange="XNAS",
                requested_start="2026-01-02",
                requested_end="2026-01-03",
                retrieved_at=retrieved_at,
                provider_library_version="fixture-1.0",
                frame=action_frame(value),
                session_resolver=FixtureSessions(),
            )

        missing_columns = action_frame(0.0).drop(
            columns=["Dividends", "Stock Splits", "Capital Gains"]
        )
        missing_result = persist_provider_frame(
            conn,
            store,
            asset_id=1,
            symbol="AAPL",
            exchange="XNAS",
            requested_start="2026-01-02",
            requested_end="2026-01-03",
            retrieved_at="2026-08-28T12:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=missing_columns,
            session_resolver=FixtureSessions(),
        )
        timeline = conn.execute(
            """
            SELECT observation.observation_kind,
                   observation.state_revision_number,
                   version.is_present, version.raw_value, version.currency
            FROM corporate_action_observations AS observation
            JOIN corporate_action_versions AS version
              USING (corporate_action_version_id)
            WHERE observation.action_type = 'dividend'
            ORDER BY observation.observation_sequence
            """
        ).fetchall()

    assert missing_result.actions_discovered == 0
    assert timeline == [
        ("initial_observation", 1, 1, 0.25, None),
        ("revision", 2, 1, 0.30, None),
        ("retraction", 3, 0, None, None),
        ("reversion", 4, 1, 0.25, None),
    ]


def test_adjusted_close_is_audit_only_not_bar_identity(tmp_path):
    db = make_db(tmp_path)
    apply_013(db)
    store = RawPriceStore(tmp_path / "raw")
    first_frame = fixture_frame().iloc[[1]].copy()
    second_frame = first_frame.copy()
    second_frame.loc[:, "Adj Close"] = 88.0

    with sqlite3.connect(db) as conn:
        first = persist_provider_frame(
            conn,
            store,
            asset_id=1,
            symbol="AAPL",
            exchange="XNAS",
            requested_start="2026-01-05",
            requested_end="2026-01-06",
            retrieved_at="2026-08-24T12:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=first_frame,
            session_resolver=FixtureSessions(),
        )
        second = persist_provider_frame(
            conn,
            store,
            asset_id=1,
            symbol="AAPL",
            exchange="XNAS",
            requested_start="2026-01-05",
            requested_end="2026-01-06",
            retrieved_at="2026-08-25T12:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=second_frame,
            session_resolver=FixtureSessions(),
        )
        observations = conn.execute(
            """
            SELECT observation.price_bar_version_id,
                   observation.observation_kind,
                   observation.state_revision_number,
                   observation.observed_adjusted_close,
                   version.adjusted_close,
                   version.normalized_bar_json
            FROM price_bar_observations AS observation
            JOIN price_bar_versions AS version
              USING (price_bar_version_id)
            ORDER BY observation.observation_sequence
            """
        ).fetchall()
        version_count = conn.execute(
            "SELECT COUNT(*) FROM price_bar_versions"
        ).fetchone()[0]

    assert first.raw_batch_id != second.raw_batch_id
    assert version_count == 1
    assert observations[0][0] == observations[1][0]
    assert [row[1:4] for row in observations] == [
        ("initial_backfill", 1, 101.0),
        ("unchanged", 1, 88.0),
    ]
    assert all(row[4] == 101.0 for row in observations)
    assert all("adjusted_close" not in json.loads(row[5]) for row in observations)


def test_exchange_aliases_do_not_create_false_revisions(tmp_path):
    db = make_db(tmp_path)
    apply_013(db)
    store = RawPriceStore(tmp_path / "raw")

    with sqlite3.connect(db) as conn:
        first = persist_provider_frame(
            conn,
            store,
            asset_id=1,
            symbol="AAPL",
            exchange="XNAS",
            requested_start="2026-01-01",
            requested_end="2026-01-06",
            retrieved_at="2026-08-24T12:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=fixture_frame(),
            session_resolver=FixtureSessions(),
        )
        second = persist_provider_frame(
            conn,
            store,
            asset_id=1,
            symbol="AAPL",
            exchange="NASDAQ",
            requested_start="2026-01-01",
            requested_end="2026-01-06",
            retrieved_at="2026-08-25T12:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=fixture_frame(),
            session_resolver=FixtureSessions(),
        )
        exchanges = conn.execute(
            "SELECT DISTINCT exchange FROM price_bar_versions"
        ).fetchall()
        kinds = conn.execute(
            """
            SELECT observation_kind
            FROM price_bar_observations
            WHERE trading_day = '2026-01-02'
            ORDER BY observation_sequence
            """
        ).fetchall()

    assert first.raw_batch_id == second.raw_batch_id
    assert exchanges == [("XNAS",)]
    assert kinds == [("initial_backfill",), ("unchanged",)]


def test_duplicate_trading_day_is_raw_only_and_fails_quality(tmp_path):
    db = make_db(tmp_path)
    apply_013(db)
    duplicate = fixture_frame().iloc[[0, 0]].copy()
    duplicate.iloc[1, duplicate.columns.get_loc("Close")] = 102.5

    with sqlite3.connect(db) as conn:
        result = persist_provider_frame(
            conn,
            RawPriceStore(tmp_path / "raw"),
            asset_id=1,
            symbol="AAPL",
            exchange="XNAS",
            requested_start="2026-01-02",
            requested_end="2026-01-03",
            retrieved_at="2026-08-24T12:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=duplicate,
            session_resolver=FixtureSessions(),
        )
        quality = conn.execute(
            """
            SELECT check_name, check_status, observed_value
            FROM price_quality_results
            WHERE quality_run_id = ?
              AND check_name IN (
                  'duplicate_trading_days',
                  'missing_expected_sessions'
              )
            ORDER BY check_name
            """,
            (result.quality_run_id,),
        ).fetchall()
        counts = (
            conn.execute("SELECT COUNT(*) FROM raw_price_batches").fetchone()[0],
            conn.execute("SELECT COUNT(*) FROM price_bar_versions").fetchone()[0],
            conn.execute(
                "SELECT COUNT(*) FROM corporate_action_versions"
            ).fetchone()[0],
        )

    assert result.bars_discovered == 0
    assert result.bars_duplicate_trading_day == 2
    assert counts == (1, 0, 0)
    assert quality == [
        ("duplicate_trading_days", "fail", 1.0),
        ("missing_expected_sessions", "fail", 1.0),
    ]


def test_run_pilot_marks_base_exception_as_failed(tmp_path, monkeypatch):
    db = make_db(tmp_path)
    apply_013(db)

    def interrupted(*args, **kwargs):
        raise KeyboardInterrupt("operator stop")

    monkeypatch.setattr(
        "ingestion.prices.yahoo_daily_v1.fetch_yahoo_daily",
        interrupted,
    )
    with pytest.raises(KeyboardInterrupt, match="operator stop"):
        run_pilot(
            db=db,
            raw_root=tmp_path / "raw",
            ticker="AAPL",
            requested_start="2026-01-01",
            requested_end="2026-01-06",
            exchange_override=None,
            max_days=10,
        )

    with sqlite3.connect(db) as conn:
        run = conn.execute(
            """
            SELECT status, error_count, error_json
            FROM source_ingestion_runs
            WHERE mode = 'yahoo_daily_pilot_v1'
            """
        ).fetchone()

    assert run[0:2] == ("failed", 1)
    assert json.loads(run[2])["error_type"] == "KeyboardInterrupt"


def test_real_calendar_resolver_supports_aapl_1980_backfill(tmp_path):
    db = make_db(tmp_path)
    apply_013(db)
    frame = pd.DataFrame(
        {
            "Open": [0.128348],
            "High": [0.129464],
            "Low": [0.128348],
            "Close": [0.128348],
            "Adj Close": [0.098834],
            "Volume": [469033600],
            "Dividends": [0.0],
            "Stock Splits": [0.0],
        },
        index=pd.DatetimeIndex(
            ["1980-12-12 00:00:00-05:00"],
            name="Date",
        ),
    )

    with sqlite3.connect(db) as conn:
        result = persist_provider_frame(
            conn,
            RawPriceStore(tmp_path / "raw"),
            asset_id=1,
            symbol="AAPL",
            exchange="NASDAQ",
            requested_start="1980-12-12",
            requested_end="1980-12-13",
            retrieved_at="2026-08-24T12:00:00+00:00",
            provider_library_version="fixture-1.0",
            frame=frame,
        )
        bar = conn.execute(
            """
            SELECT trading_day, exchange, calendar_name,
                   bar_start_utc, bar_end_utc
            FROM price_bar_versions
            """
        ).fetchone()
        coverage = conn.execute(
            """
            SELECT check_status, observed_value
            FROM price_quality_results
            WHERE quality_run_id = ?
              AND check_name = 'missing_expected_sessions'
            """,
            (result.quality_run_id,),
        ).fetchone()

    assert result.bars_inserted == 1
    assert bar == (
        "1980-12-12",
        "XNAS",
        "XNAS",
        "1980-12-12T14:30:00+00:00",
        "1980-12-12T21:00:00+00:00",
    )
    assert coverage == ("pass", 0.0)

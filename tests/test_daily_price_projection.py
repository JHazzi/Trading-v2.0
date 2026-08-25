import sqlite3

import pytest

from features.market.daily_price_projection import load_daily_projection


def make_db():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE assets(asset_id INTEGER PRIMARY KEY, ticker TEXT UNIQUE);
        CREATE TABLE price_bar_versions(
            price_bar_version_id TEXT PRIMARY KEY,
            open REAL, high REAL, low REAL, close REAL, volume REAL,
            adjusted_close REAL,
            bar_start_utc TEXT NOT NULL,
            bar_end_utc TEXT NOT NULL
        );
        CREATE TABLE price_bar_observations(
            price_observation_id TEXT PRIMARY KEY,
            price_bar_version_id TEXT NOT NULL,
            batch_retrieval_id TEXT NOT NULL,
            source_id TEXT NOT NULL,
            asset_id INTEGER NOT NULL,
            trading_day TEXT NOT NULL,
            observed_at TEXT NOT NULL,
            available_at TEXT NOT NULL,
            point_in_time_verified INTEGER NOT NULL,
            observation_kind TEXT NOT NULL,
            observation_sequence INTEGER NOT NULL
        );
        CREATE TABLE price_quality_runs(
            quality_run_id TEXT PRIMARY KEY,
            batch_retrieval_id TEXT NOT NULL,
            status TEXT NOT NULL
        );
        CREATE TABLE price_quality_results(
            quality_result_id TEXT PRIMARY KEY,
            quality_run_id TEXT NOT NULL,
            asset_id INTEGER NOT NULL,
            check_status TEXT NOT NULL
        );
        INSERT INTO assets VALUES (1, 'AAPL');

        INSERT INTO price_bar_versions VALUES
          ('v1', 100, 102, 99, 101, 1000, 101, '2026-08-20T13:30:00+00:00', '2026-08-20T20:00:00+00:00'),
          ('v2', 100, 103, 99, 102, 1000, 102, '2026-08-20T13:30:00+00:00', '2026-08-20T20:00:00+00:00');

        INSERT INTO price_bar_observations VALUES
          ('o1','v1','b1','yahoo_finance',1,'2026-08-20',
           '2026-08-24T10:00:00+00:00','2026-08-20T20:00:00+00:00',
           0,'initial_backfill',1),
          ('o2','v2','b2','yahoo_finance',1,'2026-08-20',
           '2026-08-25T10:00:00+00:00','2026-08-25T10:00:00+00:00',
           1,'revision',2);

        INSERT INTO price_quality_runs VALUES
          ('q1','b1','completed'),
          ('q2','b2','completed');
        INSERT INTO price_quality_results VALUES
          ('r1','q1',1,'pass'),
          ('r2','q2',1,'pass');
        """
    )
    return conn


def test_target_final_uses_latest_revision():
    conn = make_db()
    rows = load_daily_projection(
        conn,
        asset_id=1,
        start_day="2026-08-20",
        end_day="2026-08-20",
        mode="target_final",
    )
    assert len(rows) == 1
    assert rows[0].close == 102
    assert rows[0].observation_sequence == 2


def test_research_asof_uses_information_available_at_cutoff():
    conn = make_db()
    rows = load_daily_projection(
        conn,
        asset_id=1,
        start_day="2026-08-20",
        end_day="2026-08-20",
        mode="research_asof",
        as_of="2026-08-21T00:00:00+00:00",
    )
    assert len(rows) == 1
    assert rows[0].close == 101
    assert rows[0].point_in_time_verified is False


def test_strict_pit_does_not_claim_backfill_is_verified():
    conn = make_db()
    early = load_daily_projection(
        conn,
        asset_id=1,
        start_day="2026-08-20",
        end_day="2026-08-20",
        mode="strict_pit",
        as_of="2026-08-21T00:00:00+00:00",
    )
    assert early == []

    late = load_daily_projection(
        conn,
        asset_id=1,
        start_day="2026-08-20",
        end_day="2026-08-20",
        mode="strict_pit",
        as_of="2026-08-26T00:00:00+00:00",
    )
    assert len(late) == 1
    assert late[0].close == 102


def test_quality_fail_excludes_observation():
    conn = make_db()
    conn.execute(
        "UPDATE price_quality_results SET check_status='fail' WHERE quality_run_id='q2'"
    )
    rows = load_daily_projection(
        conn,
        asset_id=1,
        start_day="2026-08-20",
        end_day="2026-08-20",
        mode="target_final",
    )
    assert len(rows) == 1
    assert rows[0].close == 101

from __future__ import annotations

import sqlite3
from pathlib import Path

from evaluation.market.daily_v003_foundation_audit import audit_database
from features.market.daily_v003_contract import (
    DATASET_CONTRACT,
    MIN_OWN_HISTORY_DAYS,
    as_dict,
)


def _build_db(path: Path, asset_count: int = 12, days: int = 300) -> None:
    with sqlite3.connect(path) as c:
        c.executescript(
            """
            CREATE TABLE assets(
                asset_id INTEGER PRIMARY KEY,
                ticker TEXT UNIQUE NOT NULL,
                name TEXT,
                asset_type TEXT NOT NULL,
                sector TEXT,
                industry TEXT,
                country TEXT,
                currency TEXT,
                exchange TEXT,
                active INTEGER NOT NULL,
                source TEXT,
                created_at TEXT,
                updated_at TEXT
            );

            CREATE TABLE price_bar_observations(
                price_observation_id TEXT PRIMARY KEY
            );
            CREATE TABLE price_bar_versions(
                price_bar_version_id TEXT PRIMARY KEY
            );
            CREATE TABLE price_quality_runs(
                quality_run_id TEXT PRIMARY KEY
            );
            CREATE TABLE price_quality_results(
                quality_result_id TEXT PRIMARY KEY
            );

            CREATE TABLE price_bars(
                price_bar_id INTEGER PRIMARY KEY,
                asset_id INTEGER,
                timestamp TEXT,
                interval TEXT,
                close REAL
            );

            CREATE TABLE macro_observations(
                macro_observation_id INTEGER PRIMARY KEY,
                symbol TEXT,
                observation_time TEXT,
                value REAL,
                source TEXT
            );

            CREATE TABLE corporate_action_observations(
                action_observation_id TEXT PRIMARY KEY,
                asset_id INTEGER,
                action_type TEXT
            );

            CREATE TABLE daily_price_asof_configs(
                asof_contract_version TEXT,
                mode TEXT,
                cutoff_column TEXT,
                required_quality_version TEXT,
                selection_point_in_time_verified INTEGER,
                adjusted_close_role TEXT,
                disclosure TEXT
            );

            CREATE TABLE q(
                price_observation_id TEXT,
                price_bar_version_id TEXT,
                raw_batch_id TEXT,
                batch_retrieval_id TEXT,
                source_id TEXT,
                asset_id INTEGER,
                provider_symbol TEXT,
                interval TEXT,
                trading_day TEXT,
                provider_row_number INTEGER,
                observed_at TEXT,
                observed_adjusted_close REAL,
                available_at TEXT,
                availability_basis TEXT,
                observation_point_in_time_verified INTEGER,
                observation_kind TEXT,
                observation_sequence INTEGER,
                state_revision_number INTEGER,
                previous_observation_id TEXT,
                exchange TEXT,
                calendar_name TEXT,
                bar_start_utc TEXT,
                bar_end_utc TEXT,
                open REAL,
                high REAL,
                low REAL,
                close REAL,
                volume REAL,
                first_observed_adjusted_close REAL,
                bar_content_sha256 TEXT,
                normalized_bar_json TEXT,
                quality_run_id TEXT,
                quality_version TEXT,
                check_count INTEGER,
                failed_check_count INTEGER,
                warning_check_count INTEGER
            );

            CREATE VIEW daily_price_quality_gated_observations_v001 AS
            SELECT * FROM q;
            """
        )
        for i in range(asset_count):
            c.execute(
                """
                INSERT INTO assets(
                    asset_id,ticker,name,asset_type,sector,industry,country,
                    currency,exchange,active,source,created_at,updated_at
                ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    i + 1,
                    f"T{i:03d}",
                    f"Asset {i}",
                    "equity",
                    f"S{i % 3}",
                    None,
                    "US",
                    "USD",
                    "NYSE",
                    1,
                    "test",
                    "x",
                    "x",
                ),
            )

        import datetime as dt
        start = dt.date(2020, 1, 1)
        for asset in range(1, asset_count + 1):
            for j in range(days):
                day = (start + dt.timedelta(days=j)).isoformat()
                c.execute(
                    """
                    INSERT INTO q(
                        price_observation_id,price_bar_version_id,
                        raw_batch_id,batch_retrieval_id,source_id,
                        asset_id,provider_symbol,interval,trading_day,
                        provider_row_number,observed_at,
                        observed_adjusted_close,available_at,
                        availability_basis,
                        observation_point_in_time_verified,
                        observation_kind,observation_sequence,
                        state_revision_number,previous_observation_id,
                        exchange,calendar_name,bar_start_utc,bar_end_utc,
                        open,high,low,close,volume,
                        first_observed_adjusted_close,
                        bar_content_sha256,normalized_bar_json,
                        quality_run_id,quality_version,check_count,
                        failed_check_count,warning_check_count
                    ) VALUES(
                        ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                        ?,?,?,?,?,?,?,?,?,?
                    )
                    """,
                    (
                        f"o{asset}_{j}",
                        f"v{asset}_{j}",
                        "b",
                        "r",
                        "yahoo_finance",
                        asset,
                        f"T{asset-1:03d}",
                        "1d",
                        day,
                        j,
                        day + "T21:00:01Z",
                        100.0,
                        day + "T21:00:00Z",
                        "session_close_backfill_assumption",
                        0,
                        "initial_backfill",
                        1,
                        1,
                        None,
                        "NYSE",
                        "XNYS",
                        day + "T14:30:00Z",
                        day + "T21:00:00Z",
                        99,
                        102,
                        98,
                        100,
                        1_000_000,
                        100,
                        "hash",
                        "{}",
                        "qr",
                        "daily_price_quality_v2",
                        3,
                        0,
                        0,
                    ),
                )


def test_contract_is_all_asset_days():
    c = as_dict()
    assert c["dataset_contract"] == DATASET_CONTRACT
    assert c["state_clock"] == "exchange_session_close"
    assert "market_state_time<=event_state_time" in c["event_join_rule"]


def test_audit_is_read_only_and_detects_panel(tmp_path: Path):
    db = tmp_path / "market.db"
    _build_db(db)
    before = db.stat().st_size
    result = audit_database(db)
    after = db.stat().st_size
    assert result["status"] == "PASS"
    assert result["assets"]["active_equities"] == 12
    assert result["daily_quality_gated"]["assets_with_daily_data"] == 12
    assert (
        result["daily_quality_gated"]["assets_by_minimum_history_days"]["252"]
        == 12
    )
    assert result["dynamic_panel_readiness"]["latest"]["eligible_253"] == 12
    assert result["macro"]["usable_for_market_v003_features_now"] is False
    assert before == after


def test_missing_quality_view_fails_cleanly(tmp_path: Path):
    db = tmp_path / "bad.db"
    with sqlite3.connect(db) as c:
        c.execute("CREATE TABLE assets(asset_id INTEGER PRIMARY KEY)")
    result = audit_database(db)
    assert result["status"] == "FAIL"
    assert "daily_price_quality_gated_observations_v001" in (
        result["failures"]["missing_views"]
    )


def test_own_history_contract_is_long_enough_for_252_drawdown():
    assert MIN_OWN_HISTORY_DAYS == 253


def test_no_macro_without_availability_contract():
    source = Path(
        "evaluation/market/daily_v003_foundation_audit.py"
    ).read_text(encoding="utf-8")
    assert "usable_for_market_v003_features_now" in source
    assert "publication/vintage/availability" in source

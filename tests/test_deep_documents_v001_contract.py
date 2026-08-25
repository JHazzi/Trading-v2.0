from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from pipeline.event_brain_deep_documents_v001 import (
    eligible_filings,
    price_windows,
)


def test_price_window_uses_21st_distinct_day(tmp_path, monkeypatch):
    # Build a minimal DB manually because the helper above intentionally
    # mirrors production tables and we want this test narrowly focused.
    db = tmp_path / "window.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE assets(asset_id INTEGER PRIMARY KEY, ticker TEXT);
        CREATE TABLE daily_price_quality_gated_observations_v001(
            asset_id INTEGER, trading_day TEXT
        );
        """
    )
    for i, ticker in enumerate(
        ["AAPL","BAC","COST","CVX","JNJ","JPM","LLY","MSFT","WMT","XOM"],
        start=1,
    ):
        conn.execute("INSERT INTO assets VALUES (?,?)", (i,ticker))
        for day in range(1,31):
            conn.execute(
                "INSERT INTO daily_price_quality_gated_observations_v001 VALUES (?,?)",
                (i, f"2020-01-{day:02d}"),
            )
    windows = price_windows(conn, warmup_sessions=21)
    assert windows["AAPL"].ready_day == "2020-01-21"
    assert windows["AAPL"].last_day == "2020-01-30"
    assert windows["AAPL"].distinct_price_days == 30


def test_eligibility_excludes_pre_warmup_and_reuses_downloaded(tmp_path):
    db = tmp_path / "elig.db"
    conn = sqlite3.connect(db)
    conn.executescript(
        """
        CREATE TABLE assets(asset_id INTEGER PRIMARY KEY, ticker TEXT);
        CREATE TABLE daily_price_quality_gated_observations_v001(
            asset_id INTEGER, trading_day TEXT
        );
        CREATE TABLE sec_filing_metadata_versions(
            metadata_version_id TEXT PRIMARY KEY,
            filing_raw_document_id TEXT NOT NULL,
            accession_number TEXT NOT NULL,
            ticker_at_ingestion TEXT,
            form TEXT,
            acceptance_datetime TEXT,
            cik TEXT
        );
        CREATE TABLE sec_filing_metadata_observations(
            metadata_observation_id TEXT PRIMARY KEY,
            filing_raw_document_id TEXT NOT NULL,
            metadata_version_id TEXT NOT NULL,
            observation_sequence INTEGER NOT NULL,
            available_at TEXT NOT NULL
        );
        CREATE TABLE sec_filing_files(
            filing_raw_document_id TEXT NOT NULL,
            raw_document_id TEXT
        );
        CREATE TABLE sec_filing_document_metadata_selections(
            selection_id TEXT
        );
        """
    )
    tickers = ["AAPL","BAC","COST","CVX","JNJ","JPM","LLY","MSFT","WMT","XOM"]
    for i,ticker in enumerate(tickers, start=1):
        conn.execute("INSERT INTO assets VALUES (?,?)", (i,ticker))
        for day in range(1,31):
            conn.execute(
                "INSERT INTO daily_price_quality_gated_observations_v001 VALUES (?,?)",
                (i, f"2020-01-{day:02d}"),
            )

        for suffix, acceptance in (
            ("old", "2020-01-10T20:00:00+00:00"),
            ("new", "2020-01-25T20:00:00+00:00"),
        ):
            conn.execute(
                """
                INSERT INTO sec_filing_metadata_versions VALUES(
                    ?,?,?,?,?,?,?
                )
                """,
                (
                    f"v_{ticker}_{suffix}",
                    f"f_{ticker}_{suffix}",
                    f"acc-{ticker}-{suffix}",
                    ticker,
                    "8-K",
                    acceptance,
                    str(1000+i),
                ),
            )
            conn.execute(
                """
                INSERT INTO sec_filing_metadata_observations VALUES(
                    ?,?,?,?,?
                )
                """,
                (
                    f"o_{ticker}_{suffix}",
                    f"f_{ticker}_{suffix}",
                    f"v_{ticker}_{suffix}",
                    1,
                    acceptance,
                ),
            )

    # Existing content for AAPL new filing must be flagged, not duplicated.
    conn.execute(
        "INSERT INTO sec_filing_files VALUES (?,?)",
        ("f_AAPL_new", "raw-aapl"),
    )
    conn.commit()

    rows = eligible_filings(conn, warmup_sessions=21)
    assert len(rows) == 10
    assert all(x.acceptance_day == "2020-01-25" for x in rows)
    aapl = next(x for x in rows if x.ticker == "AAPL")
    bac = next(x for x in rows if x.ticker == "BAC")
    assert aapl.has_downloaded_content is True
    assert bac.has_downloaded_content is False


def test_config_declares_no_model_change():
    config = json.loads(
        Path("config/event_brain_deep_documents_v001.json").read_text()
    )
    assert config["warmup_sessions"] == 21
    assert config["skip_filings_with_existing_downloaded_content"] is True
    assert "acceptance_day>=asset_21st_quality_price_day" in (
        config["selection_contract"]
    )

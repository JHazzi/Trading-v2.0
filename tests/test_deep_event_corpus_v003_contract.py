from __future__ import annotations

import sqlite3

from pipeline.event_brain_deep_corpus_v003 import (
    _end_exclusive,
    cohort_window,
    run_id_for,
)
from ingestion.events.sec_event_normalizer_v003_deep import (
    NORMALIZATION_VERSION,
)
from features.events.event_state_v003_deep import FEATURE_VERSION
from evaluation.targets.event_reaction_targets_v003_deep import LABEL_VERSION


def test_version_contract():
    assert NORMALIZATION_VERSION == "sec_event_normalizer_v003_deep_rebuild"
    assert FEATURE_VERSION == "event_state_v003_deep"
    assert LABEL_VERSION == "event_reaction_daily_v003_deep"


def test_common_window_uses_latest_ready_and_earliest_last_day():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE assets(
            asset_id INTEGER PRIMARY KEY,
            ticker TEXT NOT NULL
        );
        CREATE TABLE daily_price_quality_gated_observations_v001(
            asset_id INTEGER NOT NULL,
            trading_day TEXT NOT NULL
        );
        """
    )

    tickers = ["AAPL","MSFT","JPM"]
    for i,ticker in enumerate(tickers,1):
        conn.execute("INSERT INTO assets VALUES (?,?)",(i,ticker))

    from datetime import date,timedelta

    base = date(2020,1,1)
    # AAPL gets 60 days from Jan 1.
    for offset in range(60):
        conn.execute(
            "INSERT INTO daily_price_quality_gated_observations_v001 VALUES (?,?)",
            (1,(base+timedelta(days=offset)).isoformat()),
        )

    # MSFT/JPM begin Jan 11. Their 21st day is Jan 31.
    for asset_id in (2,3):
        for offset in range(10,60):
            conn.execute(
                "INSERT INTO daily_price_quality_gated_observations_v001 VALUES (?,?)",
                (asset_id,(base+timedelta(days=offset)).isoformat()),
            )

    start,end,info = cohort_window(
        conn,
        tickers=tickers,
        warmup_sessions=21,
    )
    assert info["AAPL"]["ready_day"] == "2020-01-21"
    assert info["MSFT"]["ready_day"] == "2020-01-31"
    assert start == "2020-01-31"
    assert end == "2020-02-29"


def test_end_exclusive_and_run_ids():
    assert _end_exclusive("2026-08-24") == "2026-08-25"
    assert run_id_for("AAPL") == "eventbrain_deep_v003_aapl"

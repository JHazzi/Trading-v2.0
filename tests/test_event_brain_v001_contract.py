import sqlite3

import pandas as pd

from ingestion.events.sec_event_normalizer_v001 import (
    factual_events_for_filing,
    parse_items,
)
from evaluation.targets.event_reaction_targets_v001 import (
    Bar,
    _label_one,
)


def test_sec_items_are_factual_taxonomy_not_direction():
    events = factual_events_for_filing(
        "8-K", '["2.02", "5.02", "9.01"]'
    )
    assert [e["event_type"] for e in events] == [
        "financial_results_disclosure",
        "management_or_board_change",
    ]
    payload = str(events).lower()
    assert "bull" not in payload
    assert "bear" not in payload
    assert "impact" not in payload


def test_parse_items_handles_item_prefix_and_duplicates():
    assert parse_items('["Item 2.02", "2.02", "8.01"]') == [
        "2.02",
        "8.01",
    ]


def test_daily_reaction_rejects_intraday_event():
    bars = [
        Bar(
            "2026-08-20", "o1", "v1",
            "2026-08-20T13:30:00+00:00",
            "2026-08-20T20:00:00+00:00",
            100, 102, 99, 101, 1000,
        ),
        Bar(
            "2026-08-21", "o2", "v2",
            "2026-08-21T13:30:00+00:00",
            "2026-08-21T20:00:00+00:00",
            101, 104, 100, 103, 1000,
        ),
    ]
    result = _label_one(
        bars,
        set(),
        "2026-08-20T15:00:00+00:00",
        1,
        False,
    )
    assert result["status"] == "intraday_daily_resolution"


def test_daily_reaction_after_close_uses_last_completed_close():
    bars = [
        Bar(
            "2026-08-20", "o1", "v1",
            "2026-08-20T13:30:00+00:00",
            "2026-08-20T20:00:00+00:00",
            100, 102, 99, 101, 1000,
        ),
        Bar(
            "2026-08-21", "o2", "v2",
            "2026-08-21T13:30:00+00:00",
            "2026-08-21T20:00:00+00:00",
            102, 105, 101, 104, 1000,
        ),
    ]
    result = _label_one(
        bars,
        set(),
        "2026-08-20T21:00:00+00:00",
        1,
        False,
    )
    assert result["status"] == "usable"
    assert result["origin"].day == "2026-08-20"
    assert result["target"].day == "2026-08-21"
    assert round(result["return_pct"], 6) == round(
        100 * (104 / 101 - 1), 6
    )


def test_daily_reaction_excludes_corporate_action_overlap():
    bars = [
        Bar(
            "2026-08-20", "o1", "v1",
            "2026-08-20T13:30:00+00:00",
            "2026-08-20T20:00:00+00:00",
            100, 102, 99, 101, 1000,
        ),
        Bar(
            "2026-08-21", "o2", "v2",
            "2026-08-21T13:30:00+00:00",
            "2026-08-21T20:00:00+00:00",
            50, 51, 49, 50, 2000,
        ),
    ]
    result = _label_one(
        bars,
        {"2026-08-21"},
        "2026-08-20T21:00:00+00:00",
        1,
        False,
    )
    assert result["status"] == "corporate_action_overlap"

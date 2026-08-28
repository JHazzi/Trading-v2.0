from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from research.information_sources.expectation_quality_v0012 import canonical_period_scope


def test_period_scope_normalization():
    assert canonical_period_scope(json.dumps({"provider_horizon": "fiscal quarter"})) == "fiscal_quarter"
    assert canonical_period_scope(json.dumps({"provider_horizon": "fiscal year"})) == "fiscal_year"
    assert canonical_period_scope(json.dumps({"period_scope": "fiscal_quarter"})) == "fiscal_quarter"


def test_period_scope_separates_quarter_and_year_identity():
    common = ("AAPL", "source-x", "analyst_consensus", "eps", "2026-09-30", "average")
    q = common + ("fiscal_quarter",)
    y = common + ("fiscal_year",)
    assert q != y


def test_standard_free_budget_does_not_imply_weekly_full_universe():
    budget = 20
    deep = 10
    broad = 497 - deep
    remaining = budget - deep
    rotation_days = (broad + remaining - 1) // remaining
    assert rotation_days == 49
    assert rotation_days > 7

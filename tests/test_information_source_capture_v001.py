from __future__ import annotations

import json
from pathlib import Path

from ingestion.expectations.alphavantage_expectations_v001 import (
    calendar_records,
    normalize_estimate_rows,
    parse_calendar_csv,
)
from research.information_sources.provider_audit_v001 import audit_registry, load_registry

ROOT = Path(__file__).resolve().parents[1]


def test_registry_valid():
    path = ROOT / "research/information_sources/provider_registry_v001.json"
    data = load_registry(path)
    assert len(data["providers"]) >= 5
    assert audit_registry(path)["status"] == "PASS"


def test_first_provider_is_expectations_capture():
    out = audit_registry(ROOT / "research/information_sources/provider_registry_v001.json")
    assert out["first_capture_recommendation"] == "alpha_vantage_earnings"


def test_calendar_csv_parse():
    text = "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\nAAPL,Apple Inc,2026-10-29,2026-09-30,2.05,USD,post-market\n"
    rows = parse_calendar_csv(text)
    assert rows[0]["symbol"] == "AAPL"
    assert rows[0]["estimate"] == "2.05"


def test_calendar_refuses_schedule_normalization():
    text = "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\nAAPL,Apple Inc,2026-10-29,2026-09-30,2.05,USD,post-market\n"
    recs = calendar_records(text, "2026-08-27T22:00:00+00:00", "https://example.test")
    assert len(recs) == 1
    assert recs[0]["kind"] == "source_observation"


def test_source_available_at_is_retrieval_time():
    text = "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\nAAPL,Apple Inc,2026-10-29,2026-09-30,2.05,USD,post-market\n"
    retrieved = "2026-08-27T22:00:00+00:00"
    src = calendar_records(text, retrieved, "https://example.test")[0]["payload"]
    assert src["retrieved_at"] == retrieved
    assert src["available_at"] == retrieved
    assert src["strict_pit"] == 1


def test_api_key_not_in_safe_url():
    text = "symbol,name,reportDate,fiscalDateEnding,estimate,currency,timeOfTheDay\nAAPL,Apple Inc,2026-10-29,2026-09-30,2.05,USD,post-market\n"
    src = calendar_records(text, "2026-08-27T22:00:00+00:00", "https://www.alphavantage.co/query?function=EARNINGS_CALENDAR")[0]["payload"]
    assert "apikey" not in src["canonical_url"].lower()


def test_estimate_normalization():
    payload = {
        "estimates": [{
            "fiscalDateEnding": "2026-09-30",
            "horizon": "next fiscal quarter",
            "eps_estimate_average": "2.05",
            "eps_estimate_high": "2.20",
            "eps_estimate_low": "1.90",
            "eps_estimate_analyst_count": "31"
        }]
    }
    recs = normalize_estimate_rows("AAPL", payload, "source-123", "2026-08-27T22:00:00+00:00")
    assert len(recs) == 4
    assert {r["payload"]["statistic_key"] for r in recs} == {"average", "high", "low", "count"}
    assert all(r["payload"]["available_at"] == "2026-08-27T22:00:00+00:00" for r in recs)


def test_no_model_visibility_in_config():
    cfg = json.loads((ROOT / "config/information_source_capture_v001.json").read_text())
    assert cfg["feature_visibility"] == "blocked_until_separate_preregistered_experiment"
    # Provider activation controls acquisition only. It must not control model visibility.
    assert isinstance(cfg["providers"]["alpha_vantage"]["enabled"], bool)

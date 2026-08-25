from ingestion.events.sec_edgar_v4_history import (
    build_targets,
    parse_issuer_target,
)


def test_parse_explicit_issuer_target_preserves_ticker():
    result = parse_issuer_target("XOM=34088")
    assert result["ticker"] == "XOM"
    assert result["cik"].endswith("34088")
    assert result["target_resolution"] == "explicit_ticker_cik"


def test_build_targets_supports_successor_and_predecessor_same_ticker():
    mapping = {
        "AAPL": {
            "ticker": "AAPL",
            "cik": "320193",
            "title": "Apple Inc.",
        }
    }
    targets, errors = build_targets(
        mapping=mapping,
        tickers=["AAPL"],
        ciks=[],
        issuers=["XOM=2115436", "XOM=34088"],
    )
    assert errors == []
    assert len(targets) == 3

    xom = [x for x in targets if x["ticker"] == "XOM"]
    assert len(xom) == 2
    assert len({x["cik"] for x in xom}) == 2


def test_build_targets_deduplicates_exact_same_target():
    targets, errors = build_targets(
        mapping={},
        tickers=[],
        ciks=[],
        issuers=["XOM=34088", "xom=0000034088"],
    )
    assert errors == []
    assert len(targets) == 1



def test_v4_uses_canonical_metadata_version_reference():
    from pathlib import Path

    source = Path(
        "ingestion/events/sec_edgar_v4_history.py"
    ).read_text(encoding="utf-8")

    assert "canonical_metadata_version_reference" in source
    assert "observation_normalized_raw_id" in source


def test_audit_filters_to_configured_sec_forms():
    from pathlib import Path

    source = Path(
        "pipeline/event_brain_deep_history_v001.py"
    ).read_text(encoding="utf-8")

    assert "allowed_forms" in source
    assert 'str(row["form"]).upper() in allowed_forms' in source

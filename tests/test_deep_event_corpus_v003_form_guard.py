from pathlib import Path

from ingestion.events.sec_event_normalizer_v003_deep import (
    factual_events_for_filing,
)
from pipeline.event_brain_deep_corpus_v003 import config


def test_deep_config_allows_only_selected_sec_forms():
    allowed = {str(x).upper() for x in config()["forms"]}
    assert allowed == {
        "8-K", "8-K/A", "10-Q", "10-Q/A", "10-K", "10-K/A"
    }
    assert "4" not in allowed
    assert "4/A" not in allowed


def test_normalizer_source_has_pre_identity_form_guard():
    source = Path(
        "ingestion/events/sec_event_normalizer_v003_deep.py"
    ).read_text(encoding="utf-8")

    guard_pos = source.index(
        "normalized_form = str(metadata[\"form\"]).strip().upper()"
    )
    identity_pos = source.index(
        "identity_key = (",
        guard_pos,
    )
    assert guard_pos < identity_pos
    assert "skipped_non_cohort_filings += 1" in source
    assert "allowed_form_set" in source


def test_form4_is_taxonomically_supported_but_not_in_deep_cohort():
    # General SEC taxonomy can still understand Form 4. The V003 cohort
    # restriction belongs to experiment selection, not global taxonomy.
    event = factual_events_for_filing("4", None)[0]
    assert event["event_type"] == "insider_ownership_disclosure"
    allowed = {str(x).upper() for x in config()["forms"]}
    assert "4" not in allowed


def test_pipeline_captures_clustering_stdout_and_passes_form_allowlist():
    source = Path(
        "pipeline/event_brain_deep_corpus_v003.py"
    ).read_text(encoding="utf-8")
    assert "capture_output=True" in source
    assert "allowed_forms=allowed_forms" in source

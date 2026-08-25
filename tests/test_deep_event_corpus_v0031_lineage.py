from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ingestion.events.sec_event_normalizer_v003_deep import (
    NORMALIZATION_VERSION,
    _sec_evidence,
    sec_lineage_audit,
)
from features.events.event_state_v003_deep import FEATURE_VERSION
from evaluation.targets.event_reaction_targets_v003_deep import LABEL_VERSION


def _schema(conn: sqlite3.Connection) -> None:
    conn.executescript(
        """
        CREATE TABLE event_cluster_memberships(
            membership_id TEXT PRIMARY KEY,
            clustering_run_id TEXT NOT NULL,
            cluster_id TEXT NOT NULL,
            evidence_type TEXT NOT NULL,
            evidence_available_at TEXT NOT NULL,
            availability_is_point_in_time INTEGER NOT NULL,
            decision_order INTEGER NOT NULL,
            metadata_json TEXT
        );
        CREATE TABLE event_cluster_raw_membership_refs(
            membership_id TEXT PRIMARY KEY,
            raw_document_id TEXT NOT NULL
        );
        CREATE TABLE event_cluster_sec_observation_refs(
            membership_id TEXT NOT NULL,
            observation_id TEXT NOT NULL,
            observed_at TEXT NOT NULL
        );
        CREATE TABLE sec_filing_file_versions(
            filing_raw_document_id TEXT NOT NULL,
            raw_document_id TEXT NOT NULL
        );
        CREATE TABLE sec_filings(
            raw_document_id TEXT PRIMARY KEY,
            accession_number TEXT NOT NULL
        );
        CREATE TABLE sec_filing_file_observations(
            observation_id TEXT PRIMARY KEY,
            raw_document_id TEXT NOT NULL,
            observed_at TEXT NOT NULL
        );
        """
    )


def _insert_backfill(
    conn: sqlite3.Connection,
    *,
    pit: int = 0,
    add_cutoff_ref: bool = False,
) -> None:
    conn.execute(
        """
        INSERT INTO event_cluster_memberships VALUES(
            'm1','run1','c1','raw_source_document',
            '2018-02-01T21:00:00+00:00',?,1,?
        )
        """,
        (
            pit,
            json.dumps({"sec_accession_number": "0001-18-000001"}),
        ),
    )
    conn.execute(
        "INSERT INTO event_cluster_raw_membership_refs VALUES('m1','raw1')"
    )
    conn.execute(
        "INSERT INTO sec_filing_file_versions VALUES('filing1','raw1')"
    )
    conn.execute(
        "INSERT INTO sec_filings VALUES('filing1','0001-18-000001')"
    )
    # Actual retrieval happens in 2026, after the historical as-of.
    conn.execute(
        """
        INSERT INTO sec_filing_file_observations VALUES(
            'obs2026','raw1','2026-08-25T14:00:00+00:00'
        )
        """
    )
    if add_cutoff_ref:
        conn.execute(
            """
            INSERT INTO event_cluster_sec_observation_refs VALUES(
                'm1','obs_old','2018-02-01T21:00:00+00:00'
            )
            """
        )


def test_version_bump_isolated_from_bad_v003_runs():
    assert NORMALIZATION_VERSION == "sec_event_normalizer_v0031_deep_raw_lineage"
    assert FEATURE_VERSION == "event_state_v0031_deep"
    assert LABEL_VERSION == "event_reaction_daily_v0031_deep"


def test_research_backfill_maps_by_raw_lineage_even_if_retrieved_later():
    conn = sqlite3.connect(":memory:")
    _schema(conn)
    _insert_backfill(conn, pit=0, add_cutoff_ref=False)

    evidence, stats = _sec_evidence(
        conn,
        "run1",
        "2026-08-25T00:00:00+00:00",
    )
    assert len(evidence) == 1
    assert evidence[0].filing_raw_document_id == "filing1"
    assert evidence[0].evidence_pit == 0
    assert evidence[0].has_cutoff_observation_ref == 0
    assert stats["historical_retrieval_after_cutoff"] == 1


def test_strict_pit_never_survives_without_temporal_observation_ref():
    conn = sqlite3.connect(":memory:")
    _schema(conn)
    _insert_backfill(conn, pit=1, add_cutoff_ref=False)

    with pytest.raises(RuntimeError, match="strict_pit_without_temporal_ref"):
        _sec_evidence(
            conn,
            "run1",
            "2026-08-25T00:00:00+00:00",
        )


def test_strict_pit_is_allowed_when_temporal_ref_exists():
    conn = sqlite3.connect(":memory:")
    _schema(conn)
    _insert_backfill(conn, pit=1, add_cutoff_ref=True)

    evidence, stats = _sec_evidence(
        conn,
        "run1",
        "2026-08-25T00:00:00+00:00",
    )
    assert len(evidence) == 1
    assert evidence[0].evidence_pit == 1
    assert evidence[0].has_cutoff_observation_ref == 1
    assert stats["strict_pit_without_temporal_ref"] == 0


def test_lineage_audit_explains_low_observation_ref_coverage():
    conn = sqlite3.connect(":memory:")
    _schema(conn)
    _insert_backfill(conn, pit=0, add_cutoff_ref=False)

    result = sec_lineage_audit(
        conn,
        "run1",
        "2026-08-25T00:00:00+00:00",
    )
    assert result["status"] == "PASS"
    assert result["raw_memberships"] == 1
    assert result["mapped_memberships"] == 1
    assert result["cutoff_sec_observation_refs"] == 0
    assert result["stats"]["historical_retrieval_after_cutoff"] == 1


def test_config_keeps_predictive_assumptions_out():
    cfg = json.loads(
        Path("config/deep_event_corpus_v003.json").read_text()
    )
    assert cfg["normalization"]["hardcoded_direction"] is False
    assert cfg["normalization"]["hardcoded_reliability"] is False
    assert cfg["science"]["strict_pit_claim"] is False

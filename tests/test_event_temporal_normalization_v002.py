import sqlite3

from ingestion.events.sec_event_normalizer_v002 import (
    FilingEvidence,
    _metadata_asof,
    _temporal_anchor,
)


def test_metadata_selection_uses_evidence_time_not_normalization_time():
    conn = sqlite3.connect(":memory:")
    conn.row_factory = sqlite3.Row
    conn.executescript(
        """
        CREATE TABLE sec_filing_metadata_versions(
            metadata_version_id TEXT PRIMARY KEY,
            filing_raw_document_id TEXT NOT NULL,
            accession_number TEXT,
            cik TEXT,
            form TEXT,
            filing_date TEXT,
            acceptance_datetime TEXT,
            report_date TEXT,
            primary_document TEXT,
            primary_doc_description TEXT,
            is_amendment INTEGER,
            items_json TEXT,
            entity_name TEXT,
            ticker_at_ingestion TEXT,
            metadata_content_sha256 TEXT,
            normalized_metadata_json TEXT,
            parser_version TEXT,
            first_observed_at TEXT,
            first_retrieved_at TEXT,
            provenance_status TEXT,
            metadata_json TEXT,
            normalized_raw_document_id TEXT
        );
        CREATE TABLE sec_filing_metadata_observations(
            metadata_observation_id TEXT PRIMARY KEY,
            filing_raw_document_id TEXT NOT NULL,
            metadata_version_id TEXT NOT NULL,
            available_at TEXT NOT NULL,
            availability_is_point_in_time INTEGER NOT NULL,
            observation_sequence INTEGER NOT NULL,
            observation_kind TEXT NOT NULL
        );

        INSERT INTO sec_filing_metadata_versions(
            metadata_version_id,filing_raw_document_id,
            accession_number,cik,form,acceptance_datetime,
            is_amendment,items_json,ticker_at_ingestion,
            normalized_raw_document_id
        ) VALUES
          ('v1','f1','acc','1','8-K',
           '2024-01-02T21:00:00+00:00',0,'["2.02"]','AAPL','r1'),
          ('v2','f1','acc','1','8-K',
           '2024-01-02T21:00:00+00:00',0,'["2.02"]','AAPL','r2');

        INSERT INTO sec_filing_metadata_observations VALUES
          ('o1','f1','v1','2024-01-02T21:00:00+00:00',0,1,'initial'),
          ('o2','f1','v2','2026-08-24T20:00:00+00:00',1,2,'unchanged');
        """
    )

    row = _metadata_asof(
        conn,
        "f1",
        "2024-01-02T21:00:00+00:00",
    )
    assert row["metadata_observation_id"] == "o1"
    assert row["availability_is_point_in_time"] == 0


def test_temporal_anchor_preserves_historical_non_pit():
    evidence = [
        FilingEvidence(
            cluster_id="c1",
            membership_id="m1",
            evidence_available_at="2024-01-02T21:00:00+00:00",
            evidence_pit=0,
            filing_raw_document_id="f1",
        )
    ]
    available, pit, first = _temporal_anchor(
        "2024-01-02T21:00:00+00:00",
        0,
        evidence,
    )
    assert available.startswith("2024-01-02T21:00:00")
    assert first == "2024-01-02T21:00:00+00:00"
    assert pit == 0


def test_temporal_anchor_waits_for_later_evidence():
    evidence = [
        FilingEvidence(
            cluster_id="c1",
            membership_id="m1",
            evidence_available_at="2024-01-02T21:05:00+00:00",
            evidence_pit=1,
            filing_raw_document_id="f1",
        )
    ]
    available, pit, _ = _temporal_anchor(
        "2024-01-02T21:00:00+00:00",
        1,
        evidence,
    )
    assert available.startswith("2024-01-02T21:05:00")
    assert pit == 1

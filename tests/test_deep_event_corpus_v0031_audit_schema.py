from __future__ import annotations

import sqlite3
from pathlib import Path


def test_audit_does_not_query_nonexistent_filings_considered_column():
    source = Path(
        "evaluation/events/deep_corpus_audit_v003.py"
    ).read_text(encoding="utf-8")

    assert "nr.filings_considered" not in source
    assert "persisted_filings_with_evidence" in source
    assert "persisted_evidence_semantics" in source


def test_migration_017_contract_has_no_filings_considered():
    # Mirror the persisted contract that exists in migration 017:
    # filings_considered is a runtime return metric, not a DB column.
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE event_normalization_runs(
            normalization_run_id TEXT PRIMARY KEY,
            normalization_version TEXT NOT NULL,
            clustering_run_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            as_of TEXT NOT NULL,
            selection_json TEXT NOT NULL,
            clusters_considered INTEGER NOT NULL DEFAULT 0,
            events_observed INTEGER NOT NULL DEFAULT 0,
            evidence_semantics_written INTEGER NOT NULL DEFAULT 0,
            error_json TEXT
        );
        """
    )
    columns = {
        row[1]
        for row in conn.execute(
            "PRAGMA table_info(event_normalization_runs)"
        )
    }
    assert "filings_considered" not in columns
    assert "clusters_considered" in columns
    assert "events_observed" in columns


def test_persisted_filing_coverage_query_works_without_runtime_column():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE event_normalization_runs(
            normalization_run_id TEXT PRIMARY KEY,
            normalization_version TEXT NOT NULL,
            clustering_run_id TEXT NOT NULL,
            started_at TEXT NOT NULL,
            finished_at TEXT,
            status TEXT NOT NULL,
            as_of TEXT NOT NULL,
            selection_json TEXT NOT NULL,
            clusters_considered INTEGER NOT NULL DEFAULT 0,
            events_observed INTEGER NOT NULL DEFAULT 0,
            evidence_semantics_written INTEGER NOT NULL DEFAULT 0,
            error_json TEXT
        );
        CREATE TABLE event_evidence_semantics(
            evidence_semantic_id TEXT PRIMARY KEY,
            normalization_run_id TEXT NOT NULL,
            membership_id TEXT NOT NULL
        );
        CREATE TABLE event_cluster_raw_membership_refs(
            membership_id TEXT PRIMARY KEY,
            raw_document_id TEXT NOT NULL
        );
        CREATE TABLE sec_filing_file_versions(
            filing_raw_document_id TEXT NOT NULL,
            raw_document_id TEXT NOT NULL
        );
        """
    )
    conn.execute(
        """
        INSERT INTO event_normalization_runs VALUES(
            'nr1','v1','cr1','2026-01-01',NULL,'completed',
            '2026-01-01','{}',2,2,3,NULL
        )
        """
    )
    conn.executemany(
        "INSERT INTO event_evidence_semantics VALUES(?,?,?)",
        [
            ("s1","nr1","m1"),
            ("s2","nr1","m2"),
            ("s3","nr1","m3"),
        ],
    )
    conn.executemany(
        "INSERT INTO event_cluster_raw_membership_refs VALUES(?,?)",
        [("m1","r1"),("m2","r2"),("m3","r3")],
    )
    conn.executemany(
        "INSERT INTO sec_filing_file_versions VALUES(?,?)",
        [("f1","r1"),("f1","r2"),("f2","r3")],
    )

    row = conn.execute(
        """
        SELECT
            COUNT(*) AS semantics,
            COUNT(DISTINCT fv.filing_raw_document_id) AS filings
        FROM event_evidence_semantics ees
        JOIN event_cluster_raw_membership_refs rr
          ON rr.membership_id=ees.membership_id
        JOIN sec_filing_file_versions fv
          ON fv.raw_document_id=rr.raw_document_id
        WHERE ees.normalization_run_id='nr1'
        """
    ).fetchone()

    assert row == (3, 2)

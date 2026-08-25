from __future__ import annotations

import sqlite3
from pathlib import Path


def test_semantic_count_is_not_taken_after_file_version_join():
    source = Path(
        "evaluation/events/deep_corpus_audit_v003.py"
    ).read_text(encoding="utf-8")

    assert "WITH semantic_counts AS" in source
    assert "COUNT(*) AS persisted_evidence_semantics" in source
    assert "FROM event_evidence_semantics" in source
    assert "WITH persisted_outputs AS" not in source


def test_shared_raw_document_does_not_duplicate_semantic_count():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE event_evidence_semantics(
            evidence_semantic_id TEXT PRIMARY KEY,
            normalization_run_id TEXT NOT NULL,
            membership_id TEXT NOT NULL,
            UNIQUE(normalization_run_id,membership_id)
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
        "INSERT INTO event_evidence_semantics VALUES('s1','nr1','m1')"
    )
    conn.execute(
        "INSERT INTO event_cluster_raw_membership_refs VALUES('m1','raw1')"
    )

    # Same immutable bytes referenced by two filing-version rows. A joined
    # COUNT(*) would incorrectly report two semantics.
    conn.executemany(
        "INSERT INTO sec_filing_file_versions VALUES(?,?)",
        [("filing_a","raw1"),("filing_b","raw1")],
    )

    direct = conn.execute(
        """
        SELECT COUNT(*)
        FROM event_evidence_semantics
        WHERE normalization_run_id='nr1'
        """
    ).fetchone()[0]

    joined = conn.execute(
        """
        SELECT COUNT(*)
        FROM event_evidence_semantics ees
        JOIN event_cluster_raw_membership_refs rr
          ON rr.membership_id=ees.membership_id
        JOIN sec_filing_file_versions fv
          ON fv.raw_document_id=rr.raw_document_id
        WHERE ees.normalization_run_id='nr1'
        """
    ).fetchone()[0]

    assert direct == 1
    assert joined == 2


def test_run_membership_uniqueness_is_the_semantic_persistence_contract():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE event_evidence_semantics(
            evidence_semantic_id TEXT PRIMARY KEY,
            normalization_run_id TEXT NOT NULL,
            membership_id TEXT NOT NULL,
            UNIQUE(normalization_run_id,membership_id)
        );
        """
    )
    conn.execute(
        "INSERT INTO event_evidence_semantics VALUES('s1','nr1','m1')"
    )

    try:
        conn.execute(
            "INSERT INTO event_evidence_semantics VALUES('s2','nr1','m1')"
        )
    except sqlite3.IntegrityError:
        pass
    else:
        raise AssertionError("duplicate run/membership semantic was accepted")

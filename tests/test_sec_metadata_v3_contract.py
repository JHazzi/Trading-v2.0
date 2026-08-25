import sqlite3

from ingestion.events.sec_metadata_logic import (
    canonical_metadata_version_reference,
    classify_observation,
)


def test_a_b_a_metadata_sequence():
    assert classify_observation(None, None, "A", False) == ("initial", 1)
    assert classify_observation("A", 1, "A", True) == ("unchanged", 1)
    assert classify_observation("A", 1, "B", False) == ("revision", 2)
    assert classify_observation("B", 2, "A", True) == ("reversion", 3)


def test_legacy_016_duplicate_content_resolves_existing_composite_fk_pair():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE sec_filing_metadata_versions(
            metadata_version_id TEXT PRIMARY KEY,
            filing_raw_document_id TEXT NOT NULL,
            normalized_raw_document_id TEXT NOT NULL,
            metadata_content_sha256 TEXT NOT NULL,
            UNIQUE(filing_raw_document_id, metadata_content_sha256)
        );

        INSERT INTO sec_filing_metadata_versions(
            metadata_version_id,
            filing_raw_document_id,
            normalized_raw_document_id,
            metadata_content_sha256
        ) VALUES (
            'migration016_version',
            'legacy_filing_raw',
            'legacy_normalized_raw',
            'aaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaaa'
        );
        """
    )

    # A new writer proposes another immutable id/raw for identical content.
    # The UNIQUE contract makes INSERT OR IGNORE keep the migrated row.
    conn.execute(
        """
        INSERT OR IGNORE INTO sec_filing_metadata_versions(
            metadata_version_id,
            filing_raw_document_id,
            normalized_raw_document_id,
            metadata_content_sha256
        ) VALUES (?, ?, ?, ?)
        """,
        (
            "v3_candidate_version",
            "legacy_filing_raw",
            "v3_candidate_normalized_raw",
            "a" * 64,
        ),
    )

    version_id, normalized_raw_id = canonical_metadata_version_reference(
        conn,
        filing_raw_document_id="legacy_filing_raw",
        metadata_content_sha256="a" * 64,
    )

    assert version_id == "migration016_version"
    assert normalized_raw_id == "legacy_normalized_raw"

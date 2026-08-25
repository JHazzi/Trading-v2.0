from __future__ import annotations

import sqlite3


def classify_observation(
    previous_version_id: str | None,
    previous_state_revision: int | None,
    version_id: str,
    version_seen_before: bool,
) -> tuple[str, int]:
    """Classify immutable metadata observations as initial/unchanged/revision/reversion."""
    if previous_version_id is None:
        return "initial", 1
    revision = int(previous_state_revision or 1)
    if previous_version_id == version_id:
        return "unchanged", revision
    return ("reversion" if version_seen_before else "revision"), revision + 1


def canonical_metadata_version_reference(
    conn: sqlite3.Connection,
    *,
    filing_raw_document_id: str,
    metadata_content_sha256: str,
) -> tuple[str, str]:
    """
    Resolve the immutable metadata version/raw pair that actually exists.

    Migration 016 enforces UNIQUE(filing_raw_document_id, metadata_content_sha256).
    A legacy/migrated version can therefore win an INSERT OR IGNORE even when a
    newer writer proposes another metadata_version_id or normalized_raw_document_id.

    Observations have a composite FK to:
      (metadata_version_id, filing_raw_document_id, normalized_raw_document_id)

    so callers must use the canonical pair stored in sec_filing_metadata_versions.
    """
    row = conn.execute(
        """
        SELECT metadata_version_id, normalized_raw_document_id
        FROM sec_filing_metadata_versions
        WHERE filing_raw_document_id = ?
          AND metadata_content_sha256 = ?
        """,
        (filing_raw_document_id, metadata_content_sha256),
    ).fetchone()
    if row is None:
        raise RuntimeError(
            "No existe una versión SEC canónica después de INSERT OR IGNORE: "
            f"filing={filing_raw_document_id}, sha={metadata_content_sha256}"
        )
    return str(row[0]), str(row[1])

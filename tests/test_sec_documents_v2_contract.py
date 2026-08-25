import sqlite3

from ingestion.events.sec_filing_documents_v2 import select_filings_asof


def test_document_selection_uses_latest_metadata_asof():
    conn = sqlite3.connect(":memory:")
    conn.executescript(
        """
        CREATE TABLE sec_filing_metadata_versions(
            metadata_version_id TEXT PRIMARY KEY,
            filing_raw_document_id TEXT NOT NULL,
            accession_number TEXT NOT NULL,
            cik TEXT NOT NULL,
            form TEXT NOT NULL,
            acceptance_datetime TEXT NOT NULL,
            primary_document TEXT,
            primary_doc_description TEXT
        );
        CREATE TABLE sec_filing_metadata_observations(
            metadata_observation_id TEXT PRIMARY KEY,
            filing_raw_document_id TEXT NOT NULL,
            metadata_version_id TEXT NOT NULL,
            available_at TEXT NOT NULL,
            availability_is_point_in_time INTEGER NOT NULL,
            observation_sequence INTEGER NOT NULL
        );

        INSERT INTO sec_filing_metadata_versions VALUES
          ('v1','f1','0000000001-20-000001','1','8-K',
           '2020-01-01T12:00:00+00:00','old.htm','old'),
          ('v2','f1','0000000001-20-000001','1','8-K',
           '2020-01-01T12:00:00+00:00','new.htm','new');

        INSERT INTO sec_filing_metadata_observations VALUES
          ('o1','f1','v1','2020-01-01T12:00:00+00:00',0,1),
          ('o2','f1','v2','2026-08-24T20:00:00+00:00',1,2);
        """
    )

    chosen = {}
    rows = select_filings_asof(
        conn,
        accessions=[],
        max_filings=10,
        as_of="2021-01-01T00:00:00+00:00",
        selected_metadata=chosen,
    )
    assert len(rows) == 1
    assert rows[0].primary_document == "old.htm"

    chosen = {}
    rows = select_filings_asof(
        conn,
        accessions=[],
        max_filings=10,
        as_of="2026-08-25T00:00:00+00:00",
        selected_metadata=chosen,
    )
    assert rows[0].primary_document == "new.htm"

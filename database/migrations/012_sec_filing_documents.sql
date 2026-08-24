-- 012_sec_filing_documents.sql
-- Versioned SEC filing inventories and downloaded document observations.
-- Columns on sec_filing_files are added defensively by apply_migration_012.py.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sec_filing_inventory_snapshots (
    filing_raw_document_id TEXT NOT NULL,
    inventory_raw_document_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    file_count INTEGER NOT NULL CHECK (file_count >= 0),
    retrieval_run_id TEXT,
    metadata_json TEXT,
    PRIMARY KEY(filing_raw_document_id, inventory_raw_document_id),
    FOREIGN KEY(filing_raw_document_id)
        REFERENCES sec_filings(raw_document_id)
        ON DELETE CASCADE,
    FOREIGN KEY(inventory_raw_document_id)
        REFERENCES raw_source_documents(raw_document_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(retrieval_run_id)
        REFERENCES source_ingestion_runs(run_id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_sec_inventory_snapshots_filing_time
ON sec_filing_inventory_snapshots(filing_raw_document_id, observed_at);

CREATE TABLE IF NOT EXISTS sec_filing_file_versions (
    filing_raw_document_id TEXT NOT NULL,
    sequence_number TEXT NOT NULL,
    document_name TEXT NOT NULL,
    raw_document_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    retrieval_run_id TEXT,
    version_status TEXT NOT NULL
        CHECK (version_status IN (
            'canonical',
            'identical_rerun',
            'revision_observed'
        )),
    metadata_json TEXT,
    PRIMARY KEY(
        filing_raw_document_id,
        sequence_number,
        document_name,
        raw_document_id
    ),
    FOREIGN KEY(
        filing_raw_document_id,
        sequence_number,
        document_name
    ) REFERENCES sec_filing_files(
        filing_raw_document_id,
        sequence_number,
        document_name
    ) ON DELETE CASCADE,
    FOREIGN KEY(raw_document_id)
        REFERENCES raw_source_documents(raw_document_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(retrieval_run_id)
        REFERENCES source_ingestion_runs(run_id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_sec_file_versions_raw
ON sec_filing_file_versions(raw_document_id);

CREATE INDEX IF NOT EXISTS idx_sec_file_versions_observed
ON sec_filing_file_versions(filing_raw_document_id, observed_at);

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES ('012', 'sec_filing_documents');

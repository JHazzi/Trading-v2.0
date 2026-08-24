-- 014_sec_filing_observations.sql
-- Temporal observations for SEC filing indexes and file contents.
--
-- Migration 012 introduced unique content/version rows. This additive delta
-- records every retrieval separately so identical reruns and A -> B -> A
-- response sequences remain observable without duplicating content bytes.
-- The inventory_status column is added defensively by apply_migration_014.py.

CREATE TABLE IF NOT EXISTS sec_filing_inventory_observations (
    observation_id TEXT PRIMARY KEY,
    filing_raw_document_id TEXT NOT NULL,
    inventory_raw_document_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    file_count INTEGER NOT NULL CHECK (file_count >= 0),
    retrieval_run_id TEXT,
    metadata_json TEXT,
    UNIQUE(filing_raw_document_id, retrieval_run_id),
    FOREIGN KEY(filing_raw_document_id)
        REFERENCES sec_filings(raw_document_id)
        ON DELETE CASCADE,
    FOREIGN KEY(
        filing_raw_document_id,
        inventory_raw_document_id
    ) REFERENCES sec_filing_inventory_snapshots(
        filing_raw_document_id,
        inventory_raw_document_id
    ) ON DELETE CASCADE,
    FOREIGN KEY(retrieval_run_id)
        REFERENCES source_ingestion_runs(run_id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_sec_inventory_observations_filing_time
ON sec_filing_inventory_observations(filing_raw_document_id, observed_at);

CREATE TABLE IF NOT EXISTS sec_filing_file_observations (
    observation_id TEXT PRIMARY KEY,
    filing_raw_document_id TEXT NOT NULL,
    sequence_number TEXT NOT NULL,
    document_name TEXT NOT NULL,
    raw_document_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    retrieval_run_id TEXT,
    observation_status TEXT NOT NULL
        CHECK (observation_status IN (
            'canonical_first_seen',
            'canonical_rerun',
            'revision_first_seen',
            'revision_rerun'
        )),
    metadata_json TEXT,
    UNIQUE(
        filing_raw_document_id,
        sequence_number,
        document_name,
        retrieval_run_id
    ),
    FOREIGN KEY(
        filing_raw_document_id,
        sequence_number,
        document_name,
        raw_document_id
    ) REFERENCES sec_filing_file_versions(
        filing_raw_document_id,
        sequence_number,
        document_name,
        raw_document_id
    ) ON DELETE CASCADE,
    FOREIGN KEY(retrieval_run_id)
        REFERENCES source_ingestion_runs(run_id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_sec_file_observations_filing_time
ON sec_filing_file_observations(filing_raw_document_id, observed_at);

CREATE INDEX IF NOT EXISTS idx_sec_file_observations_raw
ON sec_filing_file_observations(raw_document_id);

-- Preserve the best temporal evidence available from databases that already
-- received migration 012. Those tables could retain only one row per unique
-- content, so this is explicitly labelled as a migrated legacy observation.
INSERT OR IGNORE INTO sec_filing_inventory_observations(
    observation_id,
    filing_raw_document_id,
    inventory_raw_document_id,
    observed_at,
    parser_version,
    file_count,
    retrieval_run_id,
    metadata_json
)
SELECT
    'legacy-inventory-' || lower(hex(
        filing_raw_document_id || char(0) || inventory_raw_document_id
    )),
    filing_raw_document_id,
    inventory_raw_document_id,
    observed_at,
    parser_version,
    file_count,
    retrieval_run_id,
    json_object('migrated_from', 'sec_filing_inventory_snapshots')
FROM sec_filing_inventory_snapshots;

INSERT OR IGNORE INTO sec_filing_file_observations(
    observation_id,
    filing_raw_document_id,
    sequence_number,
    document_name,
    raw_document_id,
    observed_at,
    retrieval_run_id,
    observation_status,
    metadata_json
)
SELECT
    'legacy-file-' || lower(hex(
        filing_raw_document_id || char(0) || sequence_number || char(0) ||
        document_name || char(0) || raw_document_id
    )),
    filing_raw_document_id,
    sequence_number,
    document_name,
    raw_document_id,
    observed_at,
    retrieval_run_id,
    CASE version_status
        WHEN 'canonical' THEN 'canonical_first_seen'
        WHEN 'revision_observed' THEN 'revision_first_seen'
        ELSE 'canonical_rerun'
    END,
    json_object('migrated_from', 'sec_filing_file_versions')
FROM sec_filing_file_versions;

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES ('014', 'sec_filing_observations');

-- 011_source_document_foundation.sql
-- Immutable source-document lineage for Event Brain v0.2.
-- This migration is additive and does not transform legacy news/events.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS ingestion_sources (
    source_id TEXT PRIMARY KEY,
    source_name TEXT NOT NULL,
    source_type TEXT NOT NULL,
    base_url TEXT,
    terms_url TEXT,
    access_method TEXT NOT NULL,
    rate_limit_per_second REAL,
    enabled INTEGER NOT NULL DEFAULT 1
        CHECK (enabled IN (0, 1)),
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS source_ingestion_runs (
    run_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    mode TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    checkpoint_before_json TEXT,
    checkpoint_after_json TEXT,
    documents_discovered INTEGER NOT NULL DEFAULT 0,
    documents_inserted INTEGER NOT NULL DEFAULT 0,
    documents_existing INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    error_json TEXT,
    FOREIGN KEY(source_id)
        REFERENCES ingestion_sources(source_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_source_ingestion_runs_source_time
ON source_ingestion_runs(source_id, started_at);

CREATE TABLE IF NOT EXISTS source_checkpoints (
    source_id TEXT NOT NULL,
    checkpoint_key TEXT NOT NULL,
    checkpoint_value TEXT,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(source_id, checkpoint_key),
    FOREIGN KEY(source_id)
        REFERENCES ingestion_sources(source_id)
        ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS raw_source_documents (
    raw_document_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    external_id TEXT NOT NULL,
    document_kind TEXT NOT NULL,
    source_url TEXT,
    canonical_url TEXT,
    published_at TEXT,
    available_at TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    modified_at TEXT,
    content_type TEXT,
    content_encoding TEXT,
    raw_sha256 TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
    parser_status TEXT NOT NULL DEFAULT 'raw',
    parser_version TEXT,
    parent_raw_document_id TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, external_id, raw_sha256),
    FOREIGN KEY(source_id)
        REFERENCES ingestion_sources(source_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(parent_raw_document_id)
        REFERENCES raw_source_documents(raw_document_id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_source_documents_lookup
ON raw_source_documents(source_id, external_id);

CREATE INDEX IF NOT EXISTS idx_raw_source_documents_available
ON raw_source_documents(available_at, source_id);

CREATE TABLE IF NOT EXISTS raw_document_assets (
    raw_document_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    role TEXT,
    linking_method TEXT NOT NULL,
    linking_version TEXT NOT NULL,
    confidence REAL,
    metadata_json TEXT,
    PRIMARY KEY(raw_document_id, asset_id, linking_version),
    FOREIGN KEY(raw_document_id)
        REFERENCES raw_source_documents(raw_document_id)
        ON DELETE CASCADE,
    FOREIGN KEY(asset_id)
        REFERENCES assets(asset_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_raw_document_assets_asset
ON raw_document_assets(asset_id, raw_document_id);

CREATE TABLE IF NOT EXISTS sec_filings (
    raw_document_id TEXT PRIMARY KEY,
    cik TEXT NOT NULL,
    accession_number TEXT NOT NULL,
    form TEXT NOT NULL,
    filing_date TEXT,
    acceptance_datetime TEXT NOT NULL,
    report_date TEXT,
    primary_document TEXT,
    primary_doc_description TEXT,
    is_amendment INTEGER NOT NULL DEFAULT 0
        CHECK (is_amendment IN (0, 1)),
    items_json TEXT,
    entity_name TEXT,
    ticker_at_ingestion TEXT,
    metadata_version TEXT NOT NULL,
    UNIQUE(accession_number),
    FOREIGN KEY(raw_document_id)
        REFERENCES raw_source_documents(raw_document_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_sec_filings_cik_time
ON sec_filings(cik, acceptance_datetime);

CREATE INDEX IF NOT EXISTS idx_sec_filings_form_time
ON sec_filings(form, acceptance_datetime);

CREATE TABLE IF NOT EXISTS sec_filing_files (
    filing_raw_document_id TEXT NOT NULL,
    sequence_number TEXT NOT NULL,
    document_name TEXT NOT NULL,
    document_type TEXT,
    description TEXT,
    source_url TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0
        CHECK (is_primary IN (0, 1)),
    raw_document_id TEXT,
    inventory_raw_document_id TEXT,
    declared_size_bytes INTEGER CHECK (declared_size_bytes >= 0),
    declared_last_modified TEXT,
    discovered_at TEXT,
    last_seen_at TEXT,
    download_status TEXT NOT NULL DEFAULT 'pending',
    inventory_status TEXT NOT NULL DEFAULT 'current'
        CHECK (inventory_status IN (
            'current', 'superseded', 'unknown_migrated'
        )),
    last_attempted_at TEXT,
    last_attempt_run_id TEXT,
    downloaded_at TEXT,
    selection_reason TEXT,
    error_json TEXT,
    metadata_json TEXT,
    PRIMARY KEY(
        filing_raw_document_id,
        sequence_number,
        document_name
    ),
    FOREIGN KEY(filing_raw_document_id)
        REFERENCES sec_filings(raw_document_id)
        ON DELETE CASCADE,
    FOREIGN KEY(raw_document_id)
        REFERENCES raw_source_documents(raw_document_id)
        ON DELETE SET NULL,
    FOREIGN KEY(inventory_raw_document_id)
        REFERENCES raw_source_documents(raw_document_id)
        ON DELETE SET NULL,
    FOREIGN KEY(last_attempt_run_id)
        REFERENCES source_ingestion_runs(run_id)
        ON DELETE SET NULL
);

INSERT INTO ingestion_sources (
    source_id,
    source_name,
    source_type,
    base_url,
    terms_url,
    access_method,
    rate_limit_per_second,
    metadata_json
)
VALUES (
    'sec_edgar',
    'SEC EDGAR',
    'regulator',
    'https://data.sec.gov',
    'https://www.sec.gov/about/privacy-information',
    'rest_and_bulk',
    5.0,
    '{"reliability_is_not_hardcoded":true}'
)
ON CONFLICT(source_id) DO UPDATE SET
    source_name = excluded.source_name,
    source_type = excluded.source_type,
    base_url = excluded.base_url,
    terms_url = excluded.terms_url,
    access_method = excluded.access_method,
    rate_limit_per_second = excluded.rate_limit_per_second,
    updated_at = CURRENT_TIMESTAMP;

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES ('011', 'source_document_foundation');


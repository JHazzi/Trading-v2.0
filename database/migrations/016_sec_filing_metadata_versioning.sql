-- 016_sec_filing_metadata_versioning.sql
-- Immutable SEC submissions metadata versions and causal retrieval observations.
--
-- `sec_filings` remains the stable, initial canonical identity introduced by
-- migration 011. Corrections from later submissions responses are appended
-- here and never overwrite that row.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS sec_submission_retrievals (
    submission_retrieval_id TEXT PRIMARY KEY,
    raw_document_id TEXT NOT NULL,
    ingestion_run_id TEXT,
    external_id TEXT NOT NULL,
    source_url TEXT NOT NULL,
    request_identity TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    provenance_status TEXT NOT NULL
        CHECK (provenance_status IN ('native', 'migrated')),
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(raw_document_id, request_identity),
    UNIQUE(ingestion_run_id, external_id),
    UNIQUE(submission_retrieval_id, raw_document_id),
    FOREIGN KEY(raw_document_id)
        REFERENCES raw_source_documents(raw_document_id) ON DELETE RESTRICT,
    FOREIGN KEY(ingestion_run_id)
        REFERENCES source_ingestion_runs(run_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_sec_submission_retrievals_external_time
ON sec_submission_retrievals(external_id, retrieved_at);

CREATE INDEX IF NOT EXISTS idx_sec_submission_retrievals_run
ON sec_submission_retrievals(ingestion_run_id);

CREATE TABLE IF NOT EXISTS sec_filing_metadata_versions (
    metadata_version_id TEXT PRIMARY KEY,
    filing_raw_document_id TEXT NOT NULL,
    normalized_raw_document_id TEXT NOT NULL,
    first_source_submissions_raw_document_id TEXT,
    accession_number TEXT NOT NULL,
    cik TEXT NOT NULL,
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
    metadata_content_sha256 TEXT NOT NULL
        CHECK (length(metadata_content_sha256) = 64),
    normalized_metadata_json TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    first_observed_at TEXT NOT NULL,
    first_retrieved_at TEXT NOT NULL,
    provenance_status TEXT NOT NULL
        CHECK (provenance_status IN ('native', 'migrated')),
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(filing_raw_document_id, metadata_content_sha256),
    UNIQUE(
        metadata_version_id,
        filing_raw_document_id,
        normalized_raw_document_id
    ),
    FOREIGN KEY(filing_raw_document_id)
        REFERENCES sec_filings(raw_document_id) ON DELETE RESTRICT,
    FOREIGN KEY(normalized_raw_document_id)
        REFERENCES raw_source_documents(raw_document_id) ON DELETE RESTRICT,
    FOREIGN KEY(first_source_submissions_raw_document_id)
        REFERENCES raw_source_documents(raw_document_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_sec_metadata_versions_accession
ON sec_filing_metadata_versions(accession_number, first_observed_at);

CREATE INDEX IF NOT EXISTS idx_sec_metadata_versions_filing
ON sec_filing_metadata_versions(
    filing_raw_document_id,
    first_observed_at,
    metadata_version_id
);

CREATE TABLE IF NOT EXISTS sec_filing_metadata_observations (
    metadata_observation_id TEXT PRIMARY KEY,
    filing_raw_document_id TEXT NOT NULL,
    metadata_version_id TEXT NOT NULL,
    normalized_raw_document_id TEXT NOT NULL,
    source_submission_retrieval_id TEXT,
    source_submissions_raw_document_id TEXT,
    ingestion_run_id TEXT,
    retrieval_identity TEXT NOT NULL,
    observation_sequence INTEGER NOT NULL
        CHECK (observation_sequence >= 1),
    state_revision_number INTEGER NOT NULL
        CHECK (state_revision_number >= 1),
    previous_observation_id TEXT,
    observation_kind TEXT NOT NULL
        CHECK (observation_kind IN (
            'initial', 'unchanged', 'revision', 'reversion'
        )),
    observed_at TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    availability_basis TEXT NOT NULL
        CHECK (availability_basis IN (
            'acceptance_datetime_initial',
            'retrieval_time_unchanged',
            'retrieval_time_revision',
            'retrieval_time_reversion',
            'migrated_acceptance_datetime'
        )),
    availability_is_point_in_time INTEGER NOT NULL
        CHECK (availability_is_point_in_time IN (0, 1)),
    provenance_status TEXT NOT NULL
        CHECK (provenance_status IN ('native', 'migrated')),
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(filing_raw_document_id, retrieval_identity),
    UNIQUE(filing_raw_document_id, observation_sequence),
    UNIQUE(
        metadata_observation_id,
        filing_raw_document_id,
        metadata_version_id
    ),
    FOREIGN KEY(
        metadata_version_id,
        filing_raw_document_id,
        normalized_raw_document_id
    ) REFERENCES sec_filing_metadata_versions(
        metadata_version_id,
        filing_raw_document_id,
        normalized_raw_document_id
    ) ON DELETE RESTRICT,
    FOREIGN KEY(
        source_submission_retrieval_id,
        source_submissions_raw_document_id
    ) REFERENCES sec_submission_retrievals(
        submission_retrieval_id, raw_document_id
    ) ON DELETE RESTRICT,
    FOREIGN KEY(source_submissions_raw_document_id)
        REFERENCES raw_source_documents(raw_document_id) ON DELETE RESTRICT,
    FOREIGN KEY(ingestion_run_id)
        REFERENCES source_ingestion_runs(run_id) ON DELETE SET NULL,
    FOREIGN KEY(previous_observation_id)
        REFERENCES sec_filing_metadata_observations(metadata_observation_id)
        ON DELETE RESTRICT,
    CHECK (
        provenance_status = 'migrated'
        OR (
            source_submission_retrieval_id IS NOT NULL
            AND source_submissions_raw_document_id IS NOT NULL
        )
    ),
    CHECK (
        (observation_sequence = 1 AND previous_observation_id IS NULL)
        OR (observation_sequence > 1 AND previous_observation_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_sec_metadata_observations_filing_sequence
ON sec_filing_metadata_observations(
    filing_raw_document_id,
    observation_sequence
);

CREATE INDEX IF NOT EXISTS idx_sec_metadata_observations_available
ON sec_filing_metadata_observations(available_at, filing_raw_document_id);

CREATE INDEX IF NOT EXISTS idx_sec_metadata_observations_run
ON sec_filing_metadata_observations(ingestion_run_id);

CREATE TABLE IF NOT EXISTS sec_filing_document_metadata_selections (
    selection_id TEXT PRIMARY KEY,
    document_ingestion_run_id TEXT NOT NULL,
    filing_raw_document_id TEXT NOT NULL,
    metadata_observation_id TEXT NOT NULL,
    metadata_version_id TEXT NOT NULL,
    selected_primary_document TEXT,
    selected_primary_doc_description TEXT,
    metadata_available_at TEXT NOT NULL,
    selected_at TEXT NOT NULL,
    selection_policy TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(document_ingestion_run_id, filing_raw_document_id),
    FOREIGN KEY(document_ingestion_run_id)
        REFERENCES source_ingestion_runs(run_id) ON DELETE RESTRICT,
    FOREIGN KEY(
        metadata_observation_id,
        filing_raw_document_id,
        metadata_version_id
    ) REFERENCES sec_filing_metadata_observations(
        metadata_observation_id,
        filing_raw_document_id,
        metadata_version_id
    ) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_sec_document_metadata_selection_observation
ON sec_filing_document_metadata_selections(metadata_observation_id);

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES ('016', 'sec_filing_metadata_versioning');

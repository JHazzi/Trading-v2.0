-- 015_deterministic_event_clustering.sql
-- Deterministic, causal document clustering foundation.
--
-- This layer records reproducible document-to-cluster decisions. It does not
-- create canonical events, shocks, source weights, directions, impacts or
-- decay assumptions.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS event_clustering_configs (
    cluster_version TEXT PRIMARY KEY,
    algorithm_name TEXT NOT NULL,
    fingerprint_version TEXT NOT NULL,
    normalization_version TEXT NOT NULL,
    configuration_sha256 TEXT NOT NULL UNIQUE,
    configuration_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_clustering_runs (
    clustering_run_id TEXT PRIMARY KEY,
    cluster_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('running', 'completed', 'failed')),
    as_of TEXT,
    selection_json TEXT NOT NULL,
    documents_considered INTEGER NOT NULL DEFAULT 0
        CHECK (documents_considered >= 0),
    fingerprints_created INTEGER NOT NULL DEFAULT 0
        CHECK (fingerprints_created >= 0),
    memberships_written INTEGER NOT NULL DEFAULT 0
        CHECK (memberships_written >= 0),
    clusters_created INTEGER NOT NULL DEFAULT 0
        CHECK (clusters_created >= 0),
    candidate_comparisons INTEGER NOT NULL DEFAULT 0
        CHECK (candidate_comparisons >= 0),
    error_json TEXT,
    FOREIGN KEY(cluster_version)
        REFERENCES event_clustering_configs(cluster_version)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_event_clustering_runs_version_time
ON event_clustering_runs(cluster_version, started_at);

CREATE TABLE IF NOT EXISTS event_document_fingerprints (
    fingerprint_id TEXT PRIMARY KEY,
    evidence_type TEXT NOT NULL
        CHECK (evidence_type IN ('news_document', 'raw_source_document')),
    evidence_id TEXT NOT NULL,
    news_id TEXT,
    raw_document_id TEXT,
    fingerprint_version TEXT NOT NULL,
    normalized_text_sha256 TEXT,
    content_sha256 TEXT NOT NULL,
    simhash64_hex TEXT,
    shingle_hashes_json TEXT NOT NULL,
    blocking_keys_json TEXT NOT NULL,
    token_count INTEGER NOT NULL CHECK (token_count >= 0),
    text_length INTEGER NOT NULL CHECK (text_length >= 0),
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,
    UNIQUE(evidence_type, evidence_id, fingerprint_version),
    CHECK (
        (evidence_type = 'news_document'
            AND news_id IS NOT NULL
            AND raw_document_id IS NULL)
        OR
        (evidence_type = 'raw_source_document'
            AND news_id IS NULL
            AND raw_document_id IS NOT NULL)
    ),
    FOREIGN KEY(news_id)
        REFERENCES news_documents(news_id)
        ON DELETE CASCADE,
    FOREIGN KEY(raw_document_id)
        REFERENCES raw_source_documents(raw_document_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_event_document_fingerprints_exact
ON event_document_fingerprints(
    fingerprint_version,
    normalized_text_sha256
);

CREATE TABLE IF NOT EXISTS event_cluster_memberships (
    membership_id TEXT PRIMARY KEY,
    clustering_run_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    fingerprint_id TEXT NOT NULL,
    evidence_type TEXT NOT NULL
        CHECK (evidence_type IN ('news_document', 'raw_source_document')),
    evidence_id TEXT NOT NULL,
    evidence_available_at TEXT NOT NULL,
    availability_basis TEXT NOT NULL,
    availability_is_point_in_time INTEGER NOT NULL
        CHECK (availability_is_point_in_time IN (0, 1)),
    decision_order INTEGER NOT NULL CHECK (decision_order >= 0),
    match_method TEXT NOT NULL
        CHECK (match_method IN (
            'anchor',
            'sec_accession_provenance',
            'exact_text',
            'near_duplicate'
        )),
    matched_membership_id TEXT,
    similarity REAL CHECK (
        similarity IS NULL OR similarity BETWEEN 0.0 AND 1.0
    ),
    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,
    UNIQUE(clustering_run_id, fingerprint_id),
    UNIQUE(clustering_run_id, decision_order),
    FOREIGN KEY(clustering_run_id)
        REFERENCES event_clustering_runs(clustering_run_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(cluster_id)
        REFERENCES event_clusters(cluster_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(fingerprint_id)
        REFERENCES event_document_fingerprints(fingerprint_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(matched_membership_id)
        REFERENCES event_cluster_memberships(membership_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_event_cluster_memberships_causal
ON event_cluster_memberships(
    clustering_run_id,
    evidence_available_at,
    decision_order
);

CREATE INDEX IF NOT EXISTS idx_event_cluster_memberships_cluster
ON event_cluster_memberships(cluster_id, evidence_available_at);

-- Typed references keep polymorphic membership rows backed by real source
-- records and retain the legacy news association independently.
CREATE TABLE IF NOT EXISTS event_cluster_news_membership_refs (
    membership_id TEXT PRIMARY KEY,
    news_id TEXT NOT NULL,
    FOREIGN KEY(membership_id)
        REFERENCES event_cluster_memberships(membership_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(news_id)
        REFERENCES news_documents(news_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cluster_news_membership_refs_news
ON event_cluster_news_membership_refs(news_id);

CREATE TABLE IF NOT EXISTS event_cluster_raw_membership_refs (
    membership_id TEXT PRIMARY KEY,
    raw_document_id TEXT NOT NULL,
    FOREIGN KEY(membership_id)
        REFERENCES event_cluster_memberships(membership_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(raw_document_id)
        REFERENCES raw_source_documents(raw_document_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cluster_raw_membership_refs_raw
ON event_cluster_raw_membership_refs(raw_document_id);

-- Retrieval observations are evidence about what bytes were seen when. They
-- do not become additional documents, so A -> B -> A remains two immutable
-- contents with three separately queryable observations.
CREATE TABLE IF NOT EXISTS event_cluster_sec_observation_refs (
    membership_id TEXT NOT NULL,
    observation_id TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    PRIMARY KEY(membership_id, observation_id),
    FOREIGN KEY(membership_id)
        REFERENCES event_cluster_memberships(membership_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(observation_id)
        REFERENCES sec_filing_file_observations(observation_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_cluster_sec_observation_refs_time
ON event_cluster_sec_observation_refs(observed_at, observation_id);

-- Temporal extent is run-scoped. event_clusters itself is an immutable
-- anchor identity and therefore keeps first_available_at = last_available_at.
CREATE VIEW IF NOT EXISTS event_clusters_by_run AS
SELECT
    membership.clustering_run_id,
    membership.cluster_id,
    MIN(membership.evidence_available_at) AS first_available_at,
    MAX(membership.evidence_available_at) AS last_available_at,
    COUNT(*) AS evidence_count
FROM event_cluster_memberships AS membership
GROUP BY membership.clustering_run_id, membership.cluster_id;

-- Run-scoped compatibility projection. The legacy global event_cluster_news
-- table is intentionally not populated by this clustering foundation.
CREATE VIEW IF NOT EXISTS event_cluster_news_by_run AS
SELECT
    membership.clustering_run_id,
    membership.cluster_id,
    news_ref.news_id,
    membership.similarity,
    membership.evidence_available_at,
    membership.availability_basis,
    membership.availability_is_point_in_time,
    membership.decision_order,
    membership.match_method,
    membership.membership_id
FROM event_cluster_memberships AS membership
JOIN event_cluster_news_membership_refs AS news_ref
  ON news_ref.membership_id = membership.membership_id;

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES ('015', 'deterministic_event_clustering');

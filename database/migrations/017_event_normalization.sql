-- 017_event_normalization.sql
-- Versioned, causal event normalization.
--
-- Purpose:
--   document/evidence clusters -> factual event identities/versions/observations
--
-- This migration intentionally does NOT encode:
--   market impact, bullish/bearish direction, source reliability,
--   economic importance, persistence/decay, expected return, or trading action.
--
-- Important temporal distinction:
--   occurred_at/event_time may be earlier than available_at.
--   available_at is the gate for whether the normalized observation may be used
--   by a prediction at time t.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS event_normalization_configs (
    normalization_version TEXT PRIMARY KEY,
    taxonomy_version TEXT NOT NULL,
    semantic_schema_version TEXT NOT NULL,
    configuration_sha256 TEXT NOT NULL UNIQUE,
    configuration_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS event_normalization_runs (
    normalization_run_id TEXT PRIMARY KEY,
    normalization_version TEXT NOT NULL,
    clustering_run_id TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL
        CHECK (status IN ('running', 'completed', 'failed')),
    as_of TEXT NOT NULL,
    selection_json TEXT NOT NULL,
    clusters_considered INTEGER NOT NULL DEFAULT 0
        CHECK (clusters_considered >= 0),
    events_observed INTEGER NOT NULL DEFAULT 0
        CHECK (events_observed >= 0),
    evidence_semantics_written INTEGER NOT NULL DEFAULT 0
        CHECK (evidence_semantics_written >= 0),
    error_json TEXT,
    FOREIGN KEY(normalization_version)
        REFERENCES event_normalization_configs(normalization_version)
        ON DELETE RESTRICT,
    FOREIGN KEY(clustering_run_id)
        REFERENCES event_clustering_runs(clustering_run_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_event_normalization_runs_time
ON event_normalization_runs(normalization_version, started_at);

-- Stable identity. This row says "which event", not what the event is believed
-- to imply economically.
CREATE TABLE IF NOT EXISTS normalized_event_identities (
    event_id TEXT PRIMARY KEY,
    identity_method TEXT NOT NULL,
    identity_key TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,
    UNIQUE(identity_method, identity_key)
);

-- Immutable semantic contents of an event.
-- Same event may acquire later corrected/revised versions.
CREATE TABLE IF NOT EXISTS normalized_event_versions (
    event_version_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    event_type TEXT NOT NULL,
    event_subtype TEXT,
    event_scope TEXT NOT NULL
        CHECK (event_scope IN (
            'company',
            'multi_company',
            'industry',
            'sector',
            'market',
            'macro',
            'regulatory',
            'cross_asset',
            'unknown'
        )),
    canonical_title TEXT,
    occurred_at TEXT,
    occurred_end_at TEXT,
    event_time_status TEXT NOT NULL
        CHECK (event_time_status IN (
            'unknown',
            'scheduled',
            'explicit_occurrence',
            'reported_occurrence',
            'period_reference',
            'inferred'
        )),
    event_time_basis TEXT,
    scheduled_for TEXT,
    resolved_status TEXT NOT NULL DEFAULT 'observed'
        CHECK (resolved_status IN (
            'scheduled',
            'observed',
            'confirmed',
            'corrected',
            'retracted',
            'cancelled',
            'unknown'
        )),
    normalized_content_sha256 TEXT NOT NULL
        CHECK (length(normalized_content_sha256) = 64),
    normalized_event_json TEXT NOT NULL,
    parser_or_model_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,
    FOREIGN KEY(event_id)
        REFERENCES normalized_event_identities(event_id)
        ON DELETE RESTRICT,
    UNIQUE(event_id, normalized_content_sha256)
);

CREATE INDEX IF NOT EXISTS idx_normalized_event_versions_event
ON normalized_event_versions(event_id, event_version_id);

CREATE INDEX IF NOT EXISTS idx_normalized_event_versions_time
ON normalized_event_versions(occurred_at, scheduled_for);

-- Observation history is separate from semantic content, so A -> B -> A is
-- reconstructible without duplicating immutable content.
CREATE TABLE IF NOT EXISTS normalized_event_observations (
    event_observation_id TEXT PRIMARY KEY,
    normalization_run_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_version_id TEXT NOT NULL,
    observation_sequence INTEGER NOT NULL CHECK (observation_sequence >= 1),
    previous_observation_id TEXT,
    observation_kind TEXT NOT NULL
        CHECK (observation_kind IN (
            'initial',
            'unchanged',
            'revision',
            'reversion'
        )),
    available_at TEXT NOT NULL,
    evidence_cutoff_at TEXT NOT NULL,
    availability_is_point_in_time INTEGER NOT NULL
        CHECK (availability_is_point_in_time IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,
    UNIQUE(normalization_run_id, event_id, observation_sequence),
    FOREIGN KEY(normalization_run_id)
        REFERENCES event_normalization_runs(normalization_run_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(event_id)
        REFERENCES normalized_event_identities(event_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(event_version_id)
        REFERENCES normalized_event_versions(event_version_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(previous_observation_id)
        REFERENCES normalized_event_observations(event_observation_id)
        ON DELETE RESTRICT,
    CHECK (
        (observation_sequence = 1 AND previous_observation_id IS NULL)
        OR
        (observation_sequence > 1 AND previous_observation_id IS NOT NULL)
    )
);

CREATE INDEX IF NOT EXISTS idx_normalized_event_observations_available
ON normalized_event_observations(available_at, event_id);

-- Many-to-many: one cluster can contain multiple economic events and the same
-- event can later be supported/corrected by evidence from multiple clusters.
CREATE TABLE IF NOT EXISTS event_cluster_event_links (
    cluster_event_link_id TEXT PRIMARY KEY,
    normalization_run_id TEXT NOT NULL,
    clustering_run_id TEXT NOT NULL,
    cluster_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_observation_id TEXT NOT NULL,
    link_role TEXT NOT NULL
        CHECK (link_role IN (
            'primary_evidence',
            'supporting_evidence',
            'contradicting_evidence',
            'correction_evidence',
            'retraction_evidence'
        )),
    linking_method TEXT NOT NULL,
    available_at TEXT NOT NULL,
    point_in_time INTEGER NOT NULL CHECK (point_in_time IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,
    UNIQUE(normalization_run_id, clustering_run_id, cluster_id, event_id, link_role),
    FOREIGN KEY(normalization_run_id)
        REFERENCES event_normalization_runs(normalization_run_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(clustering_run_id)
        REFERENCES event_clustering_runs(clustering_run_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(cluster_id)
        REFERENCES event_clusters(cluster_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(event_id)
        REFERENCES normalized_event_identities(event_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(event_observation_id)
        REFERENCES normalized_event_observations(event_observation_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_event_cluster_event_links_cluster
ON event_cluster_event_links(clustering_run_id, cluster_id, available_at);

CREATE INDEX IF NOT EXISTS idx_event_cluster_event_links_event
ON event_cluster_event_links(event_id, available_at);

-- Semantic form of the evidence, not economic truth and not source reliability.
-- Example: a document may be a forecast/opinion/rumor rather than an observed
-- fact. Classification confidence is classifier/extraction confidence only.
CREATE TABLE IF NOT EXISTS event_evidence_semantics (
    evidence_semantic_id TEXT PRIMARY KEY,
    normalization_run_id TEXT NOT NULL,
    membership_id TEXT NOT NULL,
    semantic_type TEXT NOT NULL
        CHECK (semantic_type IN (
            'observed_fact',
            'official_statement',
            'reported_fact',
            'opinion',
            'forecast',
            'rumor',
            'speculation',
            'correction',
            'retraction',
            'mixed',
            'unknown'
        )),
    semantic_method TEXT NOT NULL
        CHECK (semantic_method IN (
            'deterministic_metadata',
            'rule',
            'model',
            'manual'
        )),
    semantic_model_version TEXT,
    classification_confidence REAL
        CHECK (
            classification_confidence IS NULL
            OR classification_confidence BETWEEN 0.0 AND 1.0
        ),
    available_at TEXT NOT NULL,
    point_in_time INTEGER NOT NULL CHECK (point_in_time IN (0, 1)),
    semantic_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,
    UNIQUE(normalization_run_id, membership_id),
    FOREIGN KEY(normalization_run_id)
        REFERENCES event_normalization_runs(normalization_run_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(membership_id)
        REFERENCES event_cluster_memberships(membership_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_event_evidence_semantics_type
ON event_evidence_semantics(semantic_type, available_at);

-- Direct/explicit entity mapping only. Graph propagation is a later feature
-- layer and must not be silently converted into direct event evidence.
CREATE TABLE IF NOT EXISTS normalized_event_entity_links (
    event_entity_link_id TEXT PRIMARY KEY,
    normalization_run_id TEXT NOT NULL,
    event_observation_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    entity_role TEXT NOT NULL
        CHECK (entity_role IN (
            'subject',
            'issuer',
            'counterparty',
            'customer',
            'supplier',
            'competitor',
            'regulator',
            'industry',
            'sector',
            'market',
            'mentioned',
            'other'
        )),
    linking_method TEXT NOT NULL
        CHECK (linking_method IN (
            'deterministic_metadata',
            'explicit_text',
            'model_extraction',
            'manual'
        )),
    extraction_confidence REAL
        CHECK (
            extraction_confidence IS NULL
            OR extraction_confidence BETWEEN 0.0 AND 1.0
        ),
    available_at TEXT NOT NULL,
    point_in_time INTEGER NOT NULL CHECK (point_in_time IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,
    UNIQUE(normalization_run_id, event_observation_id, entity_id, entity_role),
    FOREIGN KEY(normalization_run_id)
        REFERENCES event_normalization_runs(normalization_run_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(event_observation_id)
        REFERENCES normalized_event_observations(event_observation_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(event_id)
        REFERENCES normalized_event_identities(event_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(entity_id)
        REFERENCES entities(entity_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_normalized_event_entity_links_entity
ON normalized_event_entity_links(entity_id, available_at);

-- Asset links are direct/evidence-backed mappings used for reaction labels.
-- Indirect graph-related assets belong to graph features, not this table.
CREATE TABLE IF NOT EXISTS normalized_event_asset_links (
    event_asset_link_id TEXT PRIMARY KEY,
    normalization_run_id TEXT NOT NULL,
    event_observation_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    asset_role TEXT NOT NULL
        CHECK (asset_role IN (
            'direct_subject',
            'issuer_asset',
            'explicitly_mentioned',
            'mapped_from_entity'
        )),
    linking_method TEXT NOT NULL,
    extraction_confidence REAL
        CHECK (
            extraction_confidence IS NULL
            OR extraction_confidence BETWEEN 0.0 AND 1.0
        ),
    available_at TEXT NOT NULL,
    point_in_time INTEGER NOT NULL CHECK (point_in_time IN (0, 1)),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,
    UNIQUE(normalization_run_id, event_observation_id, asset_id, asset_role),
    FOREIGN KEY(normalization_run_id)
        REFERENCES event_normalization_runs(normalization_run_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(event_observation_id)
        REFERENCES normalized_event_observations(event_observation_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(event_id)
        REFERENCES normalized_event_identities(event_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(asset_id)
        REFERENCES assets(asset_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_normalized_event_asset_links_asset
ON normalized_event_asset_links(asset_id, available_at);

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES ('017', 'event_normalization');

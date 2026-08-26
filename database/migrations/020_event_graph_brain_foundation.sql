-- 020_event_graph_brain_foundation.sql
-- Causal event/entity/temporal-graph foundation.
--
-- Key separation:
--   evidence != event != inference
--   relation candidate != validated relation assertion
--   structural relation != statistical/learned relation
--   graph exposure candidate != signed market impact
--
-- This migration is additive. It does not mutate existing event/model results.

PRAGMA foreign_keys = ON;

-- Extend structural ontology without assigning market sign or impact.
INSERT OR IGNORE INTO relation_types(relation_type, description, signed)
VALUES
('parent_of', 'Corporate parent relationship', 0),
('subsidiary_of', 'Corporate subsidiary relationship', 0),
('partner_of', 'Explicit business partnership relationship', 0),
('contract_party_of', 'Explicit contractual counterparty relationship', 0),
('operates_in', 'Operating/geographic exposure relationship', 0),
('produces', 'Entity produces a product/commodity/service', 0),
('uses', 'Entity uses a product/commodity/technology', 0),
('depends_on', 'Explicit operational dependency relationship', 0),
('financed_by', 'Explicit financing relationship', 0),
('member_of_industry', 'Entity belongs to an industry', 0),
('member_of_sector', 'Entity belongs to a sector', 0);

CREATE TABLE IF NOT EXISTS event_entity_link_runs_v001 (
    link_run_id TEXT PRIMARY KEY,
    link_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL
        CHECK(status IN ('running','completed','failed')),
    as_of TEXT,
    selection_json TEXT NOT NULL,
    configuration_sha256 TEXT NOT NULL,
    links_written INTEGER NOT NULL DEFAULT 0 CHECK(links_written >= 0),
    error_json TEXT
);

-- Model-visible factual link after resolution/promotion.
-- No expected_direction, impact, decay or learned weight belongs here.
CREATE TABLE IF NOT EXISTS event_entity_links_v001 (
    event_entity_link_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    entity_id INTEGER NOT NULL,
    asset_id INTEGER,
    link_role TEXT NOT NULL,
    first_available_at TEXT NOT NULL,
    availability_basis TEXT NOT NULL,
    availability_is_point_in_time INTEGER NOT NULL
        CHECK(availability_is_point_in_time IN (0,1)),
    source_kind TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    link_version TEXT NOT NULL,
    link_run_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,
    UNIQUE(
        event_id,
        entity_id,
        link_role,
        first_available_at,
        link_version
    ),
    FOREIGN KEY(entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE SET NULL,
    FOREIGN KEY(link_run_id)
        REFERENCES event_entity_link_runs_v001(link_run_id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_event_entity_links_event_time_v001
ON event_entity_links_v001(event_id, first_available_at);

CREATE INDEX IF NOT EXISTS idx_event_entity_links_entity_time_v001
ON event_entity_links_v001(entity_id, first_available_at);

-- Future entity extraction outputs live here until resolved/promoted.
CREATE TABLE IF NOT EXISTS event_entity_candidates_v001 (
    candidate_id TEXT PRIMARY KEY,
    event_id TEXT NOT NULL,
    candidate_entity_type TEXT NOT NULL,
    candidate_name TEXT NOT NULL,
    candidate_role TEXT NOT NULL,
    resolved_entity_id INTEGER,
    evidence_available_at TEXT NOT NULL,
    availability_is_point_in_time INTEGER NOT NULL
        CHECK(availability_is_point_in_time IN (0,1)),
    evidence_type TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    extractor_version TEXT NOT NULL,
    extractor_confidence REAL CHECK(
        extractor_confidence IS NULL OR
        extractor_confidence BETWEEN 0.0 AND 1.0
    ),
    evidence_span_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','promoted','rejected')),
    promotion_version TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT,
    metadata_json TEXT,
    FOREIGN KEY(resolved_entity_id)
        REFERENCES entities(entity_id)
        ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_event_entity_candidates_event_v001
ON event_entity_candidates_v001(event_id, status);

-- Candidate structural relations are extraction evidence only.
CREATE TABLE IF NOT EXISTS graph_relation_candidates_v001 (
    candidate_relation_id TEXT PRIMARY KEY,
    source_entity_id INTEGER NOT NULL,
    target_entity_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL,
    relation_layer TEXT NOT NULL DEFAULT 'structural'
        CHECK(relation_layer IN ('structural','statistical','learned')),
    evidence_available_at TEXT NOT NULL,
    availability_is_point_in_time INTEGER NOT NULL
        CHECK(availability_is_point_in_time IN (0,1)),
    evidence_type TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    evidence_sha256 TEXT,
    extractor_version TEXT NOT NULL,
    extractor_confidence REAL CHECK(
        extractor_confidence IS NULL OR
        extractor_confidence BETWEEN 0.0 AND 1.0
    ),
    evidence_span_json TEXT,
    status TEXT NOT NULL DEFAULT 'pending'
        CHECK(status IN ('pending','promoted','rejected')),
    promotion_version TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    reviewed_at TEXT,
    metadata_json TEXT,
    CHECK(source_entity_id <> target_entity_id),
    FOREIGN KEY(source_entity_id)
        REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY(target_entity_id)
        REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY(relation_type)
        REFERENCES relation_types(relation_type)
);

CREATE INDEX IF NOT EXISTS idx_graph_relation_candidates_status_v001
ON graph_relation_candidates_v001(status, evidence_available_at);

-- A validated relation assertion is a factual graph object. It deliberately
-- contains no market sign, predictive weight, reliability or impact field.
CREATE TABLE IF NOT EXISTS temporal_relation_assertions_v001 (
    relation_assertion_id TEXT PRIMARY KEY,
    source_entity_id INTEGER NOT NULL,
    target_entity_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL,
    relation_layer TEXT NOT NULL
        CHECK(relation_layer IN ('structural','statistical','learned')),
    valid_from TEXT,
    valid_to TEXT,
    assertion_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,
    CHECK(source_entity_id <> target_entity_id),
    UNIQUE(
        source_entity_id,
        target_entity_id,
        relation_type,
        relation_layer,
        valid_from,
        assertion_version
    ),
    FOREIGN KEY(source_entity_id)
        REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY(target_entity_id)
        REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY(relation_type)
        REFERENCES relation_types(relation_type)
);

CREATE INDEX IF NOT EXISTS idx_temporal_relation_assertions_source_v001
ON temporal_relation_assertions_v001(source_entity_id, relation_layer);

CREATE INDEX IF NOT EXISTS idx_temporal_relation_assertions_target_v001
ON temporal_relation_assertions_v001(target_entity_id, relation_layer);

-- Every assertion is model-visible only through temporal evidence.
-- Later retractions do not rewrite history; they add an observation.
CREATE TABLE IF NOT EXISTS temporal_relation_observations_v001 (
    relation_observation_id TEXT PRIMARY KEY,
    relation_assertion_id TEXT NOT NULL,
    observation_action TEXT NOT NULL
        CHECK(observation_action IN (
            'asserted',
            'confirmed',
            'corrected',
            'retracted'
        )),
    evidence_available_at TEXT NOT NULL,
    observed_at TEXT NOT NULL,
    availability_basis TEXT NOT NULL,
    availability_is_point_in_time INTEGER NOT NULL
        CHECK(availability_is_point_in_time IN (0,1)),
    evidence_type TEXT NOT NULL,
    source_ref TEXT NOT NULL,
    evidence_sha256 TEXT,
    observation_sequence INTEGER NOT NULL CHECK(observation_sequence >= 1),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,
    UNIQUE(relation_assertion_id, observation_sequence),
    FOREIGN KEY(relation_assertion_id)
        REFERENCES temporal_relation_assertions_v001(relation_assertion_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_temporal_relation_observations_asof_v001
ON temporal_relation_observations_v001(
    evidence_available_at,
    relation_assertion_id,
    observation_sequence
);

-- Versioned semantic outputs are separated from canonical event/evidence rows.
CREATE TABLE IF NOT EXISTS event_semantic_inference_runs_v001 (
    inference_run_id TEXT PRIMARY KEY,
    inference_version TEXT NOT NULL,
    model_name TEXT NOT NULL,
    model_version TEXT NOT NULL,
    trained_until TEXT,
    as_of TEXT,
    configuration_sha256 TEXT NOT NULL,
    configuration_json TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL
        CHECK(status IN ('running','completed','failed')),
    error_json TEXT
);

CREATE TABLE IF NOT EXISTS event_semantic_inferences_v001 (
    semantic_inference_id TEXT PRIMARY KEY,
    inference_run_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    as_of TEXT NOT NULL,
    event_type TEXT,
    event_subtype TEXT,
    event_scope TEXT,
    epistemic_type TEXT,
    scheduled_at TEXT,
    event_time TEXT,
    resolved_at TEXT,
    inference_confidence REAL CHECK(
        inference_confidence IS NULL OR
        inference_confidence BETWEEN 0.0 AND 1.0
    ),
    input_evidence_hash TEXT NOT NULL,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,
    UNIQUE(inference_run_id, event_id, as_of),
    FOREIGN KEY(inference_run_id)
        REFERENCES event_semantic_inference_runs_v001(inference_run_id)
        ON DELETE CASCADE
);

-- Reproducible graph exposure generation. These are candidate exposures only,
-- not predicted returns or signed impacts.
CREATE TABLE IF NOT EXISTS graph_propagation_runs_v001 (
    propagation_run_id TEXT PRIMARY KEY,
    propagation_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL
        CHECK(status IN ('running','completed','failed')),
    as_of TEXT,
    max_hops INTEGER NOT NULL CHECK(max_hops BETWEEN 0 AND 8),
    relation_layers_json TEXT NOT NULL,
    relation_types_json TEXT NOT NULL,
    event_selection_json TEXT NOT NULL,
    configuration_sha256 TEXT NOT NULL,
    direct_candidates_written INTEGER NOT NULL DEFAULT 0
        CHECK(direct_candidates_written >= 0),
    graph_candidates_written INTEGER NOT NULL DEFAULT 0
        CHECK(graph_candidates_written >= 0),
    error_json TEXT
);

CREATE TABLE IF NOT EXISTS graph_propagation_candidates_v001 (
    propagation_candidate_id TEXT PRIMARY KEY,
    propagation_run_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    event_available_at TEXT NOT NULL,
    source_entity_id INTEGER NOT NULL,
    target_entity_id INTEGER NOT NULL,
    target_asset_id INTEGER,
    hop_count INTEGER NOT NULL CHECK(hop_count >= 0),
    path_entity_ids_json TEXT NOT NULL,
    path_relation_assertion_ids_json TEXT NOT NULL,
    path_edge_orientations_json TEXT NOT NULL,
    exposure_kind TEXT NOT NULL
        CHECK(exposure_kind IN ('direct','graph')),
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,
    UNIQUE(
        propagation_run_id,
        event_id,
        source_entity_id,
        target_entity_id,
        target_asset_id,
        hop_count,
        exposure_kind
    ),
    FOREIGN KEY(propagation_run_id)
        REFERENCES graph_propagation_runs_v001(propagation_run_id)
        ON DELETE CASCADE,
    FOREIGN KEY(source_entity_id)
        REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY(target_entity_id)
        REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY(target_asset_id)
        REFERENCES assets(asset_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_graph_propagation_event_v001
ON graph_propagation_candidates_v001(
    propagation_run_id,
    event_id,
    hop_count
);

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES ('020', 'event_graph_brain_foundation');

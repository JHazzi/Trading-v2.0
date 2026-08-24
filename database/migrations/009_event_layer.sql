-- Migration 009: causal event/news layer.
-- Apply after migration 008.

CREATE TABLE IF NOT EXISTS event_clusters (
    cluster_id TEXT PRIMARY KEY,
    canonical_title TEXT,
    cluster_type TEXT NOT NULL DEFAULT 'unknown',
    first_seen_at TEXT,
    last_seen_at TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    cluster_version TEXT NOT NULL,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_clusters_time
ON event_clusters(first_seen_at, last_seen_at);

CREATE TABLE IF NOT EXISTS event_cluster_news (
    cluster_id TEXT NOT NULL,
    news_id TEXT NOT NULL,
    similarity REAL,
    evidence_strength REAL,
    linked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(cluster_id, news_id),
    FOREIGN KEY(cluster_id) REFERENCES event_clusters(cluster_id) ON DELETE CASCADE,
    FOREIGN KEY(news_id) REFERENCES news_documents(news_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_event_cluster_news_news
ON event_cluster_news(news_id);

CREATE TABLE IF NOT EXISTS event_states (
    event_id TEXT PRIMARY KEY,
    cluster_id TEXT,
    event_type TEXT NOT NULL,
    scheduled_at TEXT,
    event_time TEXT,
    first_seen_at TEXT,
    available_at TEXT NOT NULL,
    resolved_at TEXT,
    effective_until TEXT,
    company_scope REAL,
    industry_scope REAL,
    market_scope REAL,
    novelty REAL,
    uncertainty REAL,
    expectation_level REAL,
    surprise REAL,
    confirmation_probability REAL,
    retraction_probability REAL,
    feature_version TEXT NOT NULL,
    metadata_json TEXT,
    FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE CASCADE,
    FOREIGN KEY(cluster_id) REFERENCES event_clusters(cluster_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_event_states_available
ON event_states(available_at);

CREATE INDEX IF NOT EXISTS idx_event_states_scheduled
ON event_states(scheduled_at);

CREATE TABLE IF NOT EXISTS event_assets_v002 (
    event_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    relevance REAL,
    expected_direction REAL,
    exposure REAL,
    scope TEXT,
    PRIMARY KEY(event_id, asset_id),
    FOREIGN KEY(event_id) REFERENCES event_states(event_id) ON DELETE CASCADE,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS event_evidence (
    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    news_id TEXT,
    evidence_time TEXT,
    available_at TEXT NOT NULL,
    source_name TEXT,
    source_type TEXT,
    evidence_role TEXT,
    semantic_strength REAL,
    source_reliability REAL,
    novelty REAL,
    contradiction REAL,
    metadata_json TEXT,
    FOREIGN KEY(event_id) REFERENCES event_states(event_id) ON DELETE CASCADE,
    FOREIGN KEY(news_id) REFERENCES news_documents(news_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_event_evidence_event_time
ON event_evidence(event_id, available_at);

CREATE TABLE IF NOT EXISTS event_reaction_outcomes (
    reaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    anchor_time TEXT NOT NULL,
    horizon_scope TEXT NOT NULL,
    horizon_value INTEGER NOT NULL,
    horizon_unit TEXT NOT NULL,
    return_pct REAL,
    mfe_pct REAL,
    mae_pct REAL,
    volatility_change_pct REAL,
    overnight_return_pct REAL,
    regime_change REAL,
    source TEXT,
    outcome_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(event_id, asset_id, anchor_time, horizon_scope, horizon_value, horizon_unit, outcome_version),
    FOREIGN KEY(event_id) REFERENCES event_states(event_id) ON DELETE CASCADE,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_event_reactions_event
ON event_reaction_outcomes(event_id, asset_id, anchor_time);

CREATE TABLE IF NOT EXISTS event_source_knowledge (
    source_knowledge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    event_type TEXT,
    asset_type TEXT,
    observations INTEGER NOT NULL DEFAULT 0,
    mean_signed_reaction REAL,
    reaction_mae REAL,
    calibration_error REAL,
    confirmation_rate REAL,
    retraction_rate REAL,
    learned_reliability REAL,
    first_observed_at TEXT,
    last_observed_at TEXT,
    model_version TEXT,
    UNIQUE(source_name, event_type, asset_type, model_version)
);

CREATE INDEX IF NOT EXISTS idx_event_source_knowledge_lookup
ON event_source_knowledge(source_name, event_type);

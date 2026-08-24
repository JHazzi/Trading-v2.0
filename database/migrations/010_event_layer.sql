-- 010_event_layer.sql
-- Event Layer v0.1
-- Deliberately separates source documents, clusters, event state, and
-- realized market reaction. No model assumptions or hardcoded source weights.

CREATE TABLE IF NOT EXISTS event_clusters (
    cluster_id TEXT PRIMARY KEY,
    canonical_title TEXT,
    first_available_at TEXT NOT NULL,
    last_available_at TEXT NOT NULL,
    cluster_method TEXT NOT NULL,
    cluster_version TEXT NOT NULL,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_event_clusters_time
    ON event_clusters(first_available_at, last_available_at);

CREATE TABLE IF NOT EXISTS event_cluster_news (
    cluster_id TEXT NOT NULL,
    news_id TEXT NOT NULL,
    similarity REAL,
    evidence_strength REAL,
    assigned_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(cluster_id, news_id),
    FOREIGN KEY(cluster_id) REFERENCES event_clusters(cluster_id) ON DELETE CASCADE,
    FOREIGN KEY(news_id) REFERENCES news_documents(news_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_event_cluster_news_news
    ON event_cluster_news(news_id);

CREATE TABLE IF NOT EXISTS event_evidence (
    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    news_id TEXT,
    available_at TEXT NOT NULL,
    evidence_type TEXT NOT NULL,
    strength REAL,
    direction REAL,
    uncertainty REAL,
    metadata_json TEXT,
    FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE CASCADE,
    FOREIGN KEY(news_id) REFERENCES news_documents(news_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_event_evidence_event_time
    ON event_evidence(event_id, available_at);

CREATE TABLE IF NOT EXISTS event_states (
    event_state_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    asset_id INTEGER,
    state_time TEXT NOT NULL,
    available_at TEXT NOT NULL,
    novelty REAL,
    evidence_count INTEGER DEFAULT 0,
    source_diversity REAL,
    uncertainty REAL,
    expected_surprise REAL,
    expected_direction REAL,
    event_persistence REAL,
    event_age_seconds REAL,
    future_event_flag INTEGER NOT NULL DEFAULT 0 CHECK (future_event_flag IN (0,1)),
    feature_version TEXT NOT NULL,
    metadata_json TEXT,
    FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE CASCADE,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE,
    UNIQUE(event_id, asset_id, state_time, feature_version)
);

CREATE INDEX IF NOT EXISTS idx_event_states_asset_time
    ON event_states(asset_id, state_time);

CREATE INDEX IF NOT EXISTS idx_event_states_event_time
    ON event_states(event_id, state_time);

CREATE TABLE IF NOT EXISTS event_reaction_outcomes (
    reaction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    state_time TEXT NOT NULL,
    horizon_scope TEXT NOT NULL,
    horizon_value INTEGER NOT NULL,
    horizon_unit TEXT NOT NULL,
    return_pct REAL,
    mfe_pct REAL,
    mae_pct REAL,
    overnight_return_pct REAL,
    next_session_return_pct REAL,
    realized_volatility REAL,
    reaction_direction REAL,
    reaction_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE CASCADE,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE,
    UNIQUE(event_id, asset_id, state_time, horizon_scope, horizon_value, horizon_unit, reaction_version)
);

CREATE INDEX IF NOT EXISTS idx_event_reactions_event_asset
    ON event_reaction_outcomes(event_id, asset_id, state_time);

CREATE TABLE IF NOT EXISTS event_source_knowledge (
    knowledge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_name TEXT NOT NULL,
    event_type TEXT,
    asset_id INTEGER,
    context_key TEXT,
    observations INTEGER NOT NULL DEFAULT 0,
    mean_direction REAL,
    mean_abs_reaction REAL,
    calibration_error REAL,
    reliability REAL,
    uncertainty REAL,
    first_observed_at TEXT,
    last_observed_at TEXT,
    model_version TEXT NOT NULL,
    metadata_json TEXT,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_event_source_knowledge_source
    ON event_source_knowledge(source_name, event_type);

CREATE INDEX IF NOT EXISTS idx_event_source_knowledge_asset
    ON event_source_knowledge(asset_id, source_name);

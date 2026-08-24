PRAGMA foreign_keys = ON;

BEGIN;

-- ============================================================
-- 0. METADATA / VERSIONING
-- ============================================================

CREATE TABLE IF NOT EXISTS schema_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);

INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('schema_version', '2.0.0');
INSERT OR IGNORE INTO schema_meta(key, value) VALUES ('created_at', CURRENT_TIMESTAMP);

-- ============================================================
-- 1. ASSETS / ENTITIES
-- ============================================================

CREATE TABLE IF NOT EXISTS assets (
    asset_id INTEGER PRIMARY KEY AUTOINCREMENT,
    ticker TEXT NOT NULL UNIQUE,
    name TEXT,
    asset_type TEXT NOT NULL DEFAULT 'equity',
    sector TEXT,
    industry TEXT,
    country TEXT,
    currency TEXT,
    exchange TEXT,
    active INTEGER NOT NULL DEFAULT 1 CHECK (active IN (0,1)),
    source TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    updated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_assets_active ON assets(active);
CREATE INDEX IF NOT EXISTS idx_assets_sector ON assets(sector);

CREATE TABLE IF NOT EXISTS entities (
    entity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    entity_type TEXT NOT NULL,
    canonical_name TEXT NOT NULL,
    external_id TEXT,
    country TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(entity_type, canonical_name)
);

CREATE INDEX IF NOT EXISTS idx_entities_type ON entities(entity_type);
CREATE INDEX IF NOT EXISTS idx_entities_external ON entities(external_id);

-- Link an asset to its generic knowledge-graph entity.
CREATE TABLE IF NOT EXISTS asset_entities (
    asset_id INTEGER PRIMARY KEY,
    entity_id INTEGER NOT NULL,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE,
    FOREIGN KEY(entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE
);

-- ============================================================
-- 2. RAW MARKET DATA
-- ============================================================

CREATE TABLE IF NOT EXISTS price_bars (
    price_bar_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    interval TEXT NOT NULL,
    open REAL NOT NULL,
    high REAL NOT NULL,
    low REAL NOT NULL,
    close REAL NOT NULL,
    volume REAL,
    source TEXT NOT NULL,
    is_adjusted INTEGER NOT NULL DEFAULT 0 CHECK (is_adjusted IN (0,1)),
    ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(asset_id, timestamp, interval, source),
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_price_bars_asset_time
    ON price_bars(asset_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_price_bars_source
    ON price_bars(source);

CREATE TABLE IF NOT EXISTS price_data_quality (
    quality_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    interval TEXT NOT NULL,
    period_start TEXT NOT NULL,
    period_end TEXT NOT NULL,
    expected_rows INTEGER,
    actual_rows INTEGER,
    missing_rows INTEGER,
    duplicate_rows INTEGER,
    coverage_pct REAL,
    anomaly_count INTEGER DEFAULT 0,
    source TEXT,
    checked_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_price_quality_asset_period
    ON price_data_quality(asset_id, period_start, period_end);

-- ============================================================
-- 3. RAW NEWS
-- ============================================================

CREATE TABLE IF NOT EXISTS news_documents (
    news_id TEXT PRIMARY KEY,
    published_at TEXT,
    ingested_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    source_name TEXT,
    source_url TEXT,
    canonical_url TEXT,
    title TEXT,
    summary TEXT,
    raw_text TEXT,
    language TEXT,
    source_provider TEXT,
    source_payload_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_news_documents_published
    ON news_documents(published_at);
CREATE INDEX IF NOT EXISTS idx_news_documents_source
    ON news_documents(source_name);

CREATE TABLE IF NOT EXISTS news_assets (
    news_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    mention_strength REAL,
    role TEXT,
    PRIMARY KEY(news_id, asset_id),
    FOREIGN KEY(news_id) REFERENCES news_documents(news_id) ON DELETE CASCADE,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_news_assets_asset ON news_assets(asset_id);

-- AI/NLP-derived outputs. These are not assumed to be ground truth.
CREATE TABLE IF NOT EXISTS news_features (
    news_id TEXT PRIMARY KEY,
    sentiment REAL,
    sentiment_model TEXT,
    relevance REAL,
    relevance_model TEXT,
    novelty REAL,
    source_reliability REAL,
    economic_importance REAL,
    market_scope REAL,
    industry_scope REAL,
    company_scope REAL,
    uncertainty REAL,
    feature_version TEXT,
    generated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,
    FOREIGN KEY(news_id) REFERENCES news_documents(news_id) ON DELETE CASCADE
);

-- ============================================================
-- 4. EVENTS / EVENT CLUSTERS / EXPECTATIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS events (
    event_id TEXT PRIMARY KEY,
    event_type TEXT NOT NULL,
    canonical_title TEXT,
    first_seen_at TEXT,
    last_seen_at TEXT,
    event_time TEXT,
    status TEXT DEFAULT 'active',
    expected_outcome TEXT,
    expected_value REAL,
    confidence REAL,
    event_scope TEXT,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_events_type ON events(event_type);
CREATE INDEX IF NOT EXISTS idx_events_time ON events(event_time);

CREATE TABLE IF NOT EXISTS event_news (
    event_id TEXT NOT NULL,
    news_id TEXT NOT NULL,
    evidence_strength REAL,
    PRIMARY KEY(event_id, news_id),
    FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE CASCADE,
    FOREIGN KEY(news_id) REFERENCES news_documents(news_id) ON DELETE CASCADE
);

CREATE TABLE IF NOT EXISTS event_assets (
    event_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    relevance REAL,
    expected_direction REAL,
    PRIMARY KEY(event_id, asset_id),
    FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE CASCADE,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_event_assets_asset ON event_assets(asset_id);

CREATE TABLE IF NOT EXISTS scheduled_events (
    scheduled_event_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER,
    event_type TEXT NOT NULL,
    scheduled_time TEXT NOT NULL,
    source TEXT,
    expected_value REAL,
    expected_direction REAL,
    confidence REAL,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_scheduled_events_time
    ON scheduled_events(scheduled_time);
CREATE INDEX IF NOT EXISTS idx_scheduled_events_asset
    ON scheduled_events(asset_id, scheduled_time);

-- ============================================================
-- 5. MACRO / GLOBAL STATE
-- ============================================================

CREATE TABLE IF NOT EXISTS macro_observations (
    macro_observation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    symbol TEXT NOT NULL,
    observation_time TEXT NOT NULL,
    value REAL NOT NULL,
    source TEXT NOT NULL,
    unit TEXT,
    metadata_json TEXT,
    UNIQUE(symbol, observation_time, source)
);

CREATE INDEX IF NOT EXISTS idx_macro_symbol_time
    ON macro_observations(symbol, observation_time);

-- ============================================================
-- 6. KNOWLEDGE GRAPH
-- ============================================================

CREATE TABLE IF NOT EXISTS relation_types (
    relation_type TEXT PRIMARY KEY,
    description TEXT,
    signed INTEGER NOT NULL DEFAULT 0 CHECK (signed IN (0,1))
);

INSERT OR IGNORE INTO relation_types VALUES ('owns', 'Ownership/control relationship', 0);
INSERT OR IGNORE INTO relation_types VALUES ('supplier_of', 'Supplier relationship', 0);
INSERT OR IGNORE INTO relation_types VALUES ('customer_of', 'Customer relationship', 0);
INSERT OR IGNORE INTO relation_types VALUES ('competitor_of', 'Competitive relationship', 1);
INSERT OR IGNORE INTO relation_types VALUES ('regulated_by', 'Regulatory exposure', 1);
INSERT OR IGNORE INTO relation_types VALUES ('exposed_to', 'Economic exposure', 1);
INSERT OR IGNORE INTO relation_types VALUES ('correlated_with', 'Observed statistical correlation', 1);
INSERT OR IGNORE INTO relation_types VALUES ('leads', 'Lead relationship in time', 1);
INSERT OR IGNORE INTO relation_types VALUES ('lags', 'Lag relationship in time', 1);
INSERT OR IGNORE INTO relation_types VALUES ('learned_relation', 'ML-discovered relation', 1);

CREATE TABLE IF NOT EXISTS entity_relations (
    relation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    source_entity_id INTEGER NOT NULL,
    target_entity_id INTEGER NOT NULL,
    relation_type TEXT NOT NULL,
    weight REAL,
    confidence REAL,
    source TEXT,
    valid_from TEXT,
    valid_to TEXT,
    observed_count INTEGER DEFAULT 0,
    last_validated_at TEXT,
    metadata_json TEXT,
    UNIQUE(source_entity_id, target_entity_id, relation_type, valid_from),
    FOREIGN KEY(source_entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY(target_entity_id) REFERENCES entities(entity_id) ON DELETE CASCADE,
    FOREIGN KEY(relation_type) REFERENCES relation_types(relation_type)
);

CREATE INDEX IF NOT EXISTS idx_entity_relations_source
    ON entity_relations(source_entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_relations_target
    ON entity_relations(target_entity_id);
CREATE INDEX IF NOT EXISTS idx_entity_relations_type
    ON entity_relations(relation_type);

CREATE TABLE IF NOT EXISTS relation_evidence (
    evidence_id INTEGER PRIMARY KEY AUTOINCREMENT,
    relation_id INTEGER NOT NULL,
    evidence_time TEXT,
    evidence_type TEXT NOT NULL,
    source_ref TEXT,
    strength REAL,
    lag_seconds REAL,
    metadata_json TEXT,
    FOREIGN KEY(relation_id) REFERENCES entity_relations(relation_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_relation_evidence_relation
    ON relation_evidence(relation_id, evidence_time);

-- ============================================================
-- 7. MARKET STATE / FEATURES
-- ============================================================

CREATE TABLE IF NOT EXISTS market_state_snapshots (
    snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    last_price REAL NOT NULL,
    market_regime TEXT,
    trend_state REAL,
    volatility_state REAL,
    liquidity_state REAL,
    drawdown_pct REAL,
    distance_high_pct REAL,
    distance_low_pct REAL,
    sector_strength REAL,
    benchmark_strength REAL,
    state_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(asset_id, timestamp, state_version),
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_state_asset_time
    ON market_state_snapshots(asset_id, timestamp);

CREATE TABLE IF NOT EXISTS feature_snapshots (
    feature_snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_value REAL,
    feature_group TEXT NOT NULL,
    source TEXT,
    feature_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(asset_id, timestamp, feature_name, feature_version)
);

CREATE INDEX IF NOT EXISTS idx_feature_asset_time
    ON feature_snapshots(asset_id, timestamp);
CREATE INDEX IF NOT EXISTS idx_feature_name
    ON feature_snapshots(feature_name, feature_version);

CREATE TABLE IF NOT EXISTS event_feature_snapshots (
    event_feature_snapshot_id INTEGER PRIMARY KEY AUTOINCREMENT,
    event_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    timestamp TEXT NOT NULL,
    feature_name TEXT NOT NULL,
    feature_value REAL,
    feature_version TEXT NOT NULL,
    UNIQUE(event_id, asset_id, timestamp, feature_name, feature_version),
    FOREIGN KEY(event_id) REFERENCES events(event_id) ON DELETE CASCADE,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_event_features_event_asset_time
    ON event_feature_snapshots(event_id, asset_id, timestamp);

-- ============================================================
-- 8. LEARNED KNOWLEDGE
-- ============================================================

CREATE TABLE IF NOT EXISTS learned_knowledge (
    knowledge_id INTEGER PRIMARY KEY AUTOINCREMENT,
    knowledge_type TEXT NOT NULL,
    subject_entity_id INTEGER,
    object_entity_id INTEGER,
    feature_key TEXT,
    value REAL,
    confidence REAL,
    observations INTEGER DEFAULT 0,
    first_observed_at TEXT,
    last_observed_at TEXT,
    model_version TEXT,
    metadata_json TEXT,
    FOREIGN KEY(subject_entity_id) REFERENCES entities(entity_id) ON DELETE SET NULL,
    FOREIGN KEY(object_entity_id) REFERENCES entities(entity_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_learned_knowledge_type
    ON learned_knowledge(knowledge_type);
CREATE INDEX IF NOT EXISTS idx_learned_knowledge_entities
    ON learned_knowledge(subject_entity_id, object_entity_id);

-- ============================================================
-- 9. TARGETS / REALIZED FUTURE OUTCOMES
-- ============================================================

CREATE TABLE IF NOT EXISTS realized_outcomes (
    outcome_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    origin_time TEXT NOT NULL,
    horizon_seconds INTEGER NOT NULL,
    end_time TEXT,
    start_price REAL,
    end_price REAL,
    return_pct REAL,
    mfe_pct REAL,
    mae_pct REAL,
    min_price REAL,
    max_price REAL,
    realized_volatility REAL,
    path_json TEXT,
    source TEXT,
    target_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(asset_id, origin_time, horizon_seconds, target_version),
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_outcomes_asset_origin
    ON realized_outcomes(asset_id, origin_time);
CREATE INDEX IF NOT EXISTS idx_outcomes_horizon
    ON realized_outcomes(horizon_seconds);

-- ============================================================
-- 10. MODEL REGISTRY
-- ============================================================

CREATE TABLE IF NOT EXISTS model_registry (
    model_id INTEGER PRIMARY KEY AUTOINCREMENT,
    model_name TEXT NOT NULL,
    model_type TEXT NOT NULL,
    version TEXT NOT NULL,
    status TEXT NOT NULL DEFAULT 'candidate',
    artifact_path TEXT,
    feature_version TEXT,
    target_version TEXT,
    trained_until TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metrics_json TEXT,
    UNIQUE(model_name, version)
);

CREATE INDEX IF NOT EXISTS idx_model_registry_status
    ON model_registry(model_name, status);

-- ============================================================
-- 11. PREDICTIONS
-- ============================================================

CREATE TABLE IF NOT EXISTS predictions (
    prediction_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    created_at TEXT NOT NULL,
    horizon_seconds INTEGER NOT NULL,
    model_id INTEGER NOT NULL,
    state_snapshot_id INTEGER,
    event_context_json TEXT,
    graph_context_json TEXT,
    q05 REAL,
    q25 REAL,
    q50 REAL,
    q75 REAL,
    q95 REAL,
    probability_positive REAL,
    probability_above_cost REAL,
    probability_below_loss REAL,
    expected_mfe_pct REAL,
    expected_mae_pct REAL,
    uncertainty_score REAL,
    calibration_version TEXT,
    explanation_json TEXT,
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id) ON DELETE CASCADE,
    FOREIGN KEY(model_id) REFERENCES model_registry(model_id),
    FOREIGN KEY(state_snapshot_id) REFERENCES market_state_snapshots(snapshot_id)
);

CREATE INDEX IF NOT EXISTS idx_predictions_asset_time
    ON predictions(asset_id, created_at);
CREATE INDEX IF NOT EXISTS idx_predictions_model
    ON predictions(model_id);

CREATE TABLE IF NOT EXISTS prediction_paths (
    prediction_path_id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL,
    scenario_id INTEGER NOT NULL,
    step_index INTEGER NOT NULL,
    timestamp TEXT,
    return_pct REAL,
    price REAL,
    weight REAL,
    FOREIGN KEY(prediction_id) REFERENCES predictions(prediction_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_prediction_paths_prediction
    ON prediction_paths(prediction_id, scenario_id, step_index);

-- ============================================================
-- 12. PREDICTION OUTCOMES / DIAGNOSTICS
-- ============================================================

CREATE TABLE IF NOT EXISTS prediction_outcomes (
    prediction_id INTEGER PRIMARY KEY,
    realized_outcome_id INTEGER NOT NULL,
    directional_correct INTEGER,
    error_abs_pct REAL,
    error_sq_pct REAL,
    quantile_hit_05 INTEGER,
    quantile_hit_25 INTEGER,
    quantile_hit_50 INTEGER,
    quantile_hit_75 INTEGER,
    quantile_hit_95 INTEGER,
    calibration_error REAL,
    evaluated_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(prediction_id) REFERENCES predictions(prediction_id) ON DELETE CASCADE,
    FOREIGN KEY(realized_outcome_id) REFERENCES realized_outcomes(outcome_id)
);

CREATE TABLE IF NOT EXISTS prediction_diagnostics (
    diagnostic_id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL,
    diagnostic_type TEXT NOT NULL,
    severity REAL,
    detail_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(prediction_id) REFERENCES predictions(prediction_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_prediction_diagnostics_prediction
    ON prediction_diagnostics(prediction_id);

-- ============================================================
-- 13. TRADING / PAPER TRADING
-- ============================================================

CREATE TABLE IF NOT EXISTS broker_profiles (
    broker_id INTEGER PRIMARY KEY AUTOINCREMENT,
    broker_name TEXT NOT NULL UNIQUE,
    config_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS trading_opportunities (
    opportunity_id INTEGER PRIMARY KEY AUTOINCREMENT,
    prediction_id INTEGER NOT NULL,
    generated_at TEXT NOT NULL,
    score REAL,
    expected_net_return_pct REAL,
    risk_score REAL,
    decision TEXT NOT NULL,
    rationale_json TEXT,
    FOREIGN KEY(prediction_id) REFERENCES predictions(prediction_id) ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_opportunities_time
    ON trading_opportunities(generated_at);

CREATE TABLE IF NOT EXISTS paper_positions (
    paper_position_id INTEGER PRIMARY KEY AUTOINCREMENT,
    opportunity_id INTEGER NOT NULL,
    asset_id INTEGER NOT NULL,
    opened_at TEXT NOT NULL,
    closed_at TEXT,
    entry_price REAL NOT NULL,
    exit_price REAL,
    quantity REAL,
    gross_return_pct REAL,
    total_cost_pct REAL,
    net_return_pct REAL,
    status TEXT NOT NULL DEFAULT 'open',
    FOREIGN KEY(opportunity_id) REFERENCES trading_opportunities(opportunity_id),
    FOREIGN KEY(asset_id) REFERENCES assets(asset_id)
);

CREATE INDEX IF NOT EXISTS idx_paper_positions_status
    ON paper_positions(status, opened_at);

-- ============================================================
-- 14. INGESTION / SYSTEM AUDIT
-- ============================================================

CREATE TABLE IF NOT EXISTS ingestion_runs (
    run_id INTEGER PRIMARY KEY AUTOINCREMENT,
    worker_name TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    rows_inserted INTEGER DEFAULT 0,
    rows_skipped INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_ingestion_runs_worker_time
    ON ingestion_runs(worker_name, started_at);

CREATE TABLE IF NOT EXISTS data_lineage (
    lineage_id INTEGER PRIMARY KEY AUTOINCREMENT,
    object_type TEXT NOT NULL,
    object_id TEXT NOT NULL,
    source_type TEXT NOT NULL,
    source_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_data_lineage_object
    ON data_lineage(object_type, object_id);

COMMIT;

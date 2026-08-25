-- 019_event_brain_v001.sql
-- Model-ready causal Event Brain v0.1 foundation.
--
-- This is not another raw-ingestion layer. It persists:
--   normalized event state snapshots -> realized daily reaction labels
-- and a registry of Event Brain training experiments.
--
-- No source reliability, impact weight, bullish/bearish prior, importance,
-- persistence or decay constant is hardcoded here.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS event_state_feature_configs (
    feature_version TEXT PRIMARY KEY,
    normalization_version TEXT NOT NULL,
    state_algorithm TEXT NOT NULL,
    configuration_sha256 TEXT NOT NULL UNIQUE,
    configuration_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS normalized_event_state_snapshots (
    event_state_id TEXT PRIMARY KEY,
    normalization_run_id TEXT NOT NULL,
    event_observation_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,

    state_time TEXT NOT NULL,
    available_at TEXT NOT NULL,
    first_evidence_at TEXT NOT NULL,

    event_type TEXT NOT NULL,
    event_subtype TEXT,
    event_scope TEXT NOT NULL,
    source_signature TEXT NOT NULL,

    evidence_count INTEGER NOT NULL CHECK (evidence_count >= 0),
    distinct_cluster_count INTEGER NOT NULL CHECK (distinct_cluster_count >= 0),
    distinct_source_count INTEGER NOT NULL CHECK (distinct_source_count >= 0),
    point_in_time_evidence_fraction REAL NOT NULL
        CHECK (point_in_time_evidence_fraction BETWEEN 0.0 AND 1.0),

    semantic_observed_fact_count INTEGER NOT NULL DEFAULT 0,
    semantic_official_statement_count INTEGER NOT NULL DEFAULT 0,
    semantic_reported_fact_count INTEGER NOT NULL DEFAULT 0,
    semantic_opinion_count INTEGER NOT NULL DEFAULT 0,
    semantic_forecast_count INTEGER NOT NULL DEFAULT 0,
    semantic_rumor_count INTEGER NOT NULL DEFAULT 0,
    semantic_speculation_count INTEGER NOT NULL DEFAULT 0,
    semantic_correction_count INTEGER NOT NULL DEFAULT 0,
    semantic_retraction_count INTEGER NOT NULL DEFAULT 0,
    semantic_mixed_count INTEGER NOT NULL DEFAULT 0,
    semantic_unknown_count INTEGER NOT NULL DEFAULT 0,

    seconds_since_first_evidence REAL NOT NULL CHECK (seconds_since_first_evidence >= 0),
    event_age_seconds REAL,
    time_to_scheduled_seconds REAL,
    has_known_occurrence_time INTEGER NOT NULL CHECK (has_known_occurrence_time IN (0,1)),
    has_scheduled_time INTEGER NOT NULL CHECK (has_scheduled_time IN (0,1)),

    feature_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,

    UNIQUE(event_id, asset_id, state_time, feature_version),

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
        ON DELETE RESTRICT,
    FOREIGN KEY(feature_version)
        REFERENCES event_state_feature_configs(feature_version)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_event_state_v001_asset_time
ON normalized_event_state_snapshots(asset_id, state_time, feature_version);

CREATE INDEX IF NOT EXISTS idx_event_state_v001_event_time
ON normalized_event_state_snapshots(event_id, state_time, feature_version);

CREATE TABLE IF NOT EXISTS normalized_event_reaction_labels (
    reaction_label_id TEXT PRIMARY KEY,
    event_state_id TEXT NOT NULL,
    event_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    state_time TEXT NOT NULL,

    horizon_sessions INTEGER NOT NULL CHECK (horizon_sessions IN (1,3,5,10)),
    origin_alignment TEXT NOT NULL
        CHECK (origin_alignment = 'last_completed_session_close'),

    origin_trading_day TEXT,
    target_trading_day TEXT,
    origin_price_observation_id TEXT,
    target_price_observation_id TEXT,
    origin_price_bar_version_id TEXT,
    target_price_bar_version_id TEXT,
    origin_close REAL,
    target_close REAL,

    return_pct REAL,
    mfe_pct REAL,
    mae_pct REAL,
    realized_path_vol_pct REAL,

    market_benchmark_asset_id INTEGER,
    market_benchmark_return_pct REAL,
    market_relative_return_pct REAL,

    label_status TEXT NOT NULL CHECK (
        label_status IN (
            'usable',
            'intraday_daily_resolution',
            'corporate_action_overlap',
            'insufficient_price_history',
            'insufficient_future_sessions'
        )
    ),
    skip_reason TEXT,

    price_truth_policy TEXT NOT NULL,
    price_asof_contract_version TEXT NOT NULL,
    label_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    metadata_json TEXT,

    UNIQUE(event_state_id, horizon_sessions, label_version),

    FOREIGN KEY(event_state_id)
        REFERENCES normalized_event_state_snapshots(event_state_id)
        ON DELETE CASCADE,
    FOREIGN KEY(event_id)
        REFERENCES normalized_event_identities(event_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(asset_id)
        REFERENCES assets(asset_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(origin_price_observation_id)
        REFERENCES price_bar_observations(price_observation_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(target_price_observation_id)
        REFERENCES price_bar_observations(price_observation_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(origin_price_bar_version_id)
        REFERENCES price_bar_versions(price_bar_version_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(target_price_bar_version_id)
        REFERENCES price_bar_versions(price_bar_version_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(market_benchmark_asset_id)
        REFERENCES assets(asset_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_event_reaction_v001_horizon
ON normalized_event_reaction_labels(
    horizon_sessions, label_status, state_time
);

CREATE INDEX IF NOT EXISTS idx_event_reaction_v001_asset
ON normalized_event_reaction_labels(asset_id, horizon_sessions, state_time);

CREATE TABLE IF NOT EXISTS event_brain_training_runs (
    training_run_id TEXT PRIMARY KEY,
    model_version TEXT NOT NULL,
    horizon_sessions INTEGER NOT NULL CHECK (horizon_sessions IN (1,3,5,10)),
    event_feature_version TEXT NOT NULL,
    market_feature_version TEXT NOT NULL,
    label_version TEXT NOT NULL,

    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL CHECK (status IN ('running','completed','failed')),
    temporal_cutoff TEXT,
    train_rows INTEGER NOT NULL DEFAULT 0 CHECK (train_rows >= 0),
    test_rows INTEGER NOT NULL DEFAULT 0 CHECK (test_rows >= 0),
    oof_rows INTEGER NOT NULL DEFAULT 0 CHECK (oof_rows >= 0),

    artifact_path TEXT,
    metrics_json TEXT,
    configuration_json TEXT NOT NULL,
    error_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE INDEX IF NOT EXISTS idx_event_brain_training_v001
ON event_brain_training_runs(model_version, horizon_sessions, started_at);

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES ('019', 'event_brain_v001');

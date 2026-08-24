-- 013_daily_price_observation_foundation.sql
-- Versioned, append-only daily price observations with raw provider lineage.
-- Additive: deliberately does not read from or write to legacy price_bars.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS raw_price_batches (
    raw_batch_id TEXT PRIMARY KEY,
    source_id TEXT NOT NULL,
    source_run_id TEXT,
    asset_id INTEGER NOT NULL,
    provider_symbol TEXT NOT NULL,
    exchange TEXT NOT NULL,
    calendar_name TEXT NOT NULL,
    interval TEXT NOT NULL CHECK (interval = '1d'),
    requested_start TEXT NOT NULL,
    requested_end TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    lineage_kind TEXT NOT NULL
        CHECK (lineage_kind = 'provider_library_output'),
    is_exact_http_response INTEGER NOT NULL DEFAULT 0
        CHECK (is_exact_http_response = 0),
    provider_library_name TEXT NOT NULL,
    provider_library_version TEXT NOT NULL,
    request_json TEXT NOT NULL,
    raw_sha256 TEXT NOT NULL,
    storage_path TEXT NOT NULL,
    content_type TEXT NOT NULL DEFAULT 'application/json',
    content_encoding TEXT NOT NULL DEFAULT 'gzip',
    byte_length INTEGER NOT NULL CHECK (byte_length >= 0),
    row_count INTEGER NOT NULL CHECK (row_count >= 0),
    batch_version TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(source_id, asset_id, provider_symbol, raw_sha256, batch_version),
    FOREIGN KEY(source_id)
        REFERENCES ingestion_sources(source_id) ON DELETE RESTRICT,
    FOREIGN KEY(source_run_id)
        REFERENCES source_ingestion_runs(run_id) ON DELETE SET NULL,
    FOREIGN KEY(asset_id)
        REFERENCES assets(asset_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_raw_price_batches_asset_time
ON raw_price_batches(asset_id, retrieved_at);

CREATE INDEX IF NOT EXISTS idx_raw_price_batches_hash
ON raw_price_batches(raw_sha256);

CREATE TABLE IF NOT EXISTS raw_price_batch_retrievals (
    batch_retrieval_id TEXT PRIMARY KEY,
    raw_batch_id TEXT NOT NULL,
    source_run_id TEXT,
    retrieved_at TEXT NOT NULL,
    request_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(raw_batch_id, source_run_id),
    FOREIGN KEY(raw_batch_id)
        REFERENCES raw_price_batches(raw_batch_id) ON DELETE RESTRICT,
    FOREIGN KEY(source_run_id)
        REFERENCES source_ingestion_runs(run_id) ON DELETE SET NULL
);

CREATE INDEX IF NOT EXISTS idx_raw_price_retrievals_batch_time
ON raw_price_batch_retrievals(raw_batch_id, retrieved_at);

CREATE INDEX IF NOT EXISTS idx_raw_price_retrievals_run
ON raw_price_batch_retrievals(source_run_id);

CREATE TABLE IF NOT EXISTS price_bar_versions (
    price_bar_version_id TEXT PRIMARY KEY,
    first_raw_batch_id TEXT NOT NULL,
    first_batch_retrieval_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    provider_symbol TEXT NOT NULL,
    interval TEXT NOT NULL CHECK (interval = '1d'),
    trading_day TEXT NOT NULL,
    exchange TEXT NOT NULL,
    calendar_name TEXT NOT NULL,
    bar_start_utc TEXT NOT NULL,
    bar_end_utc TEXT NOT NULL,
    first_observed_at TEXT NOT NULL,
    open REAL,
    high REAL,
    low REAL,
    close REAL,
    volume REAL,
    adjusted_close REAL,
    provider_row_number INTEGER NOT NULL CHECK (provider_row_number >= 0),
    bar_content_sha256 TEXT NOT NULL,
    normalized_bar_json TEXT NOT NULL,
    batch_version TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(
        source_id,
        asset_id,
        interval,
        trading_day,
        bar_content_sha256
    ),
    FOREIGN KEY(first_raw_batch_id)
        REFERENCES raw_price_batches(raw_batch_id) ON DELETE RESTRICT,
    FOREIGN KEY(first_batch_retrieval_id)
        REFERENCES raw_price_batch_retrievals(batch_retrieval_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(source_id)
        REFERENCES ingestion_sources(source_id) ON DELETE RESTRICT,
    FOREIGN KEY(asset_id)
        REFERENCES assets(asset_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_price_bar_versions_asset_day
ON price_bar_versions(asset_id, trading_day, first_observed_at);

CREATE INDEX IF NOT EXISTS idx_price_bar_versions_hash
ON price_bar_versions(bar_content_sha256);

CREATE TABLE IF NOT EXISTS price_bar_observations (
    price_observation_id TEXT PRIMARY KEY,
    price_bar_version_id TEXT NOT NULL,
    raw_batch_id TEXT NOT NULL,
    batch_retrieval_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    provider_symbol TEXT NOT NULL,
    interval TEXT NOT NULL CHECK (interval = '1d'),
    trading_day TEXT NOT NULL,
    provider_row_number INTEGER NOT NULL CHECK (provider_row_number >= 0),
    observed_at TEXT NOT NULL,
    observed_adjusted_close REAL,
    available_at TEXT NOT NULL,
    availability_basis TEXT NOT NULL CHECK (
        availability_basis IN (
            'session_close_backfill_assumption',
            'retrieval_time_unchanged',
            'retrieval_time_revision',
            'retrieval_time_reversion'
        )
    ),
    point_in_time_verified INTEGER NOT NULL DEFAULT 0
        CHECK (point_in_time_verified IN (0, 1)),
    observation_kind TEXT NOT NULL CHECK (
        observation_kind IN (
            'initial_backfill',
            'unchanged',
            'revision',
            'reversion'
        )
    ),
    observation_sequence INTEGER NOT NULL CHECK (observation_sequence >= 1),
    state_revision_number INTEGER NOT NULL CHECK (state_revision_number >= 1),
    previous_observation_id TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(
        batch_retrieval_id,
        asset_id,
        interval,
        trading_day,
        provider_row_number
    ),
    FOREIGN KEY(price_bar_version_id)
        REFERENCES price_bar_versions(price_bar_version_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(raw_batch_id)
        REFERENCES raw_price_batches(raw_batch_id) ON DELETE RESTRICT,
    FOREIGN KEY(batch_retrieval_id)
        REFERENCES raw_price_batch_retrievals(batch_retrieval_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(source_id)
        REFERENCES ingestion_sources(source_id) ON DELETE RESTRICT,
    FOREIGN KEY(asset_id)
        REFERENCES assets(asset_id) ON DELETE RESTRICT,
    FOREIGN KEY(previous_observation_id)
        REFERENCES price_bar_observations(price_observation_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_price_observations_asset_day
ON price_bar_observations(
    asset_id, trading_day, observation_sequence, observed_at
);

CREATE INDEX IF NOT EXISTS idx_price_observations_available
ON price_bar_observations(asset_id, available_at);

CREATE TABLE IF NOT EXISTS corporate_action_versions (
    corporate_action_version_id TEXT PRIMARY KEY,
    first_raw_batch_id TEXT NOT NULL,
    first_batch_retrieval_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    provider_symbol TEXT NOT NULL,
    action_type TEXT NOT NULL
        CHECK (action_type IN ('dividend', 'stock_split', 'capital_gain')),
    effective_trading_day TEXT NOT NULL,
    action_time_utc TEXT NOT NULL,
    is_present INTEGER NOT NULL CHECK (is_present IN (0, 1)),
    raw_value REAL,
    currency TEXT,
    action_content_sha256 TEXT NOT NULL,
    provider_row_number INTEGER NOT NULL CHECK (provider_row_number >= 0),
    normalized_action_json TEXT NOT NULL,
    batch_version TEXT NOT NULL,
    parser_version TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(
        source_id,
        asset_id,
        action_type,
        effective_trading_day,
        action_content_sha256
    ),
    CHECK (
        (is_present = 1 AND raw_value IS NOT NULL AND raw_value != 0)
        OR (is_present = 0 AND raw_value IS NULL)
    ),
    FOREIGN KEY(first_raw_batch_id)
        REFERENCES raw_price_batches(raw_batch_id) ON DELETE RESTRICT,
    FOREIGN KEY(first_batch_retrieval_id)
        REFERENCES raw_price_batch_retrievals(batch_retrieval_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(source_id)
        REFERENCES ingestion_sources(source_id) ON DELETE RESTRICT,
    FOREIGN KEY(asset_id)
        REFERENCES assets(asset_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_action_versions_asset_day
ON corporate_action_versions(
    asset_id, effective_trading_day, action_type
);

CREATE TABLE IF NOT EXISTS corporate_action_observations (
    action_observation_id TEXT PRIMARY KEY,
    corporate_action_version_id TEXT NOT NULL,
    raw_batch_id TEXT NOT NULL,
    batch_retrieval_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    provider_symbol TEXT NOT NULL,
    action_type TEXT NOT NULL
        CHECK (action_type IN ('dividend', 'stock_split', 'capital_gain')),
    effective_trading_day TEXT NOT NULL,
    announcement_available_at TEXT,
    observed_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    availability_basis TEXT NOT NULL
        CHECK (availability_basis = 'retrieval_time_no_announcement'),
    observation_kind TEXT NOT NULL CHECK (
        observation_kind IN (
            'initial_observation',
            'unchanged',
            'revision',
            'retraction',
            'reversion'
        )
    ),
    observation_sequence INTEGER NOT NULL CHECK (observation_sequence >= 1),
    state_revision_number INTEGER NOT NULL CHECK (state_revision_number >= 1),
    previous_observation_id TEXT,
    provider_row_number INTEGER NOT NULL CHECK (provider_row_number >= 0),
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(
        batch_retrieval_id,
        asset_id,
        action_type,
        effective_trading_day,
        provider_row_number
    ),
    FOREIGN KEY(corporate_action_version_id)
        REFERENCES corporate_action_versions(corporate_action_version_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(raw_batch_id)
        REFERENCES raw_price_batches(raw_batch_id) ON DELETE RESTRICT,
    FOREIGN KEY(batch_retrieval_id)
        REFERENCES raw_price_batch_retrievals(batch_retrieval_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(source_id)
        REFERENCES ingestion_sources(source_id) ON DELETE RESTRICT,
    FOREIGN KEY(asset_id)
        REFERENCES assets(asset_id) ON DELETE RESTRICT,
    FOREIGN KEY(previous_observation_id)
        REFERENCES corporate_action_observations(action_observation_id)
        ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_corporate_actions_asset_day
ON corporate_action_observations(
    asset_id, effective_trading_day, action_type, observed_at
);

CREATE TABLE IF NOT EXISTS asset_identifier_history (
    identifier_history_id TEXT PRIMARY KEY,
    asset_id INTEGER NOT NULL,
    identifier_type TEXT NOT NULL,
    identifier_value TEXT NOT NULL,
    source_id TEXT NOT NULL,
    valid_from TEXT,
    valid_to TEXT,
    available_at TEXT NOT NULL,
    retrieved_at TEXT NOT NULL,
    is_primary INTEGER NOT NULL DEFAULT 0 CHECK (is_primary IN (0, 1)),
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (valid_to IS NULL OR valid_from IS NOT NULL),
    FOREIGN KEY(asset_id)
        REFERENCES assets(asset_id) ON DELETE RESTRICT,
    FOREIGN KEY(source_id)
        REFERENCES ingestion_sources(source_id) ON DELETE RESTRICT
);

CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_identifier_unknown_validity
ON asset_identifier_history(
    asset_id, identifier_type, identifier_value, source_id
)
WHERE valid_from IS NULL AND valid_to IS NULL;

CREATE UNIQUE INDEX IF NOT EXISTS uq_asset_identifier_known_validity
ON asset_identifier_history(
    asset_id, identifier_type, identifier_value, source_id, valid_from
)
WHERE valid_from IS NOT NULL;

CREATE INDEX IF NOT EXISTS idx_asset_identifiers_lookup
ON asset_identifier_history(identifier_type, identifier_value, available_at);

CREATE INDEX IF NOT EXISTS idx_asset_identifiers_asset_validity
ON asset_identifier_history(asset_id, valid_from, valid_to);

CREATE TABLE IF NOT EXISTS price_quality_runs (
    quality_run_id TEXT PRIMARY KEY,
    raw_batch_id TEXT NOT NULL,
    batch_retrieval_id TEXT NOT NULL,
    source_id TEXT NOT NULL,
    quality_version TEXT NOT NULL,
    started_at TEXT NOT NULL,
    finished_at TEXT NOT NULL,
    status TEXT NOT NULL CHECK (status IN ('completed', 'failed')),
    configuration_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(batch_retrieval_id, quality_version),
    FOREIGN KEY(raw_batch_id)
        REFERENCES raw_price_batches(raw_batch_id) ON DELETE RESTRICT,
    FOREIGN KEY(batch_retrieval_id)
        REFERENCES raw_price_batch_retrievals(batch_retrieval_id)
        ON DELETE RESTRICT,
    FOREIGN KEY(source_id)
        REFERENCES ingestion_sources(source_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_price_quality_runs_batch
ON price_quality_runs(raw_batch_id, quality_version);

CREATE INDEX IF NOT EXISTS idx_price_quality_runs_retrieval
ON price_quality_runs(batch_retrieval_id, quality_version);

CREATE TABLE IF NOT EXISTS price_quality_results (
    quality_result_id TEXT PRIMARY KEY,
    quality_run_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    raw_batch_id TEXT NOT NULL,
    check_name TEXT NOT NULL,
    check_status TEXT NOT NULL
        CHECK (check_status IN ('pass', 'warn', 'fail')),
    observed_value REAL,
    expected_value REAL,
    details_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    UNIQUE(quality_run_id, asset_id, check_name),
    FOREIGN KEY(quality_run_id)
        REFERENCES price_quality_runs(quality_run_id) ON DELETE RESTRICT,
    FOREIGN KEY(asset_id)
        REFERENCES assets(asset_id) ON DELETE RESTRICT,
    FOREIGN KEY(raw_batch_id)
        REFERENCES raw_price_batches(raw_batch_id) ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_price_quality_results_asset
ON price_quality_results(asset_id, check_status, check_name);

INSERT INTO ingestion_sources(
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
    'yahoo_finance',
    'Yahoo Finance via yfinance',
    'market_data_aggregator',
    'https://finance.yahoo.com',
    NULL,
    'python_provider_library',
    NULL,
    '{"lineage_kind":"provider_library_output","exact_http_bytes_preserved":false,"point_in_time_history":false,"reliability_is_not_hardcoded":true}'
)
ON CONFLICT(source_id) DO UPDATE SET
    source_name = excluded.source_name,
    source_type = excluded.source_type,
    base_url = excluded.base_url,
    access_method = excluded.access_method,
    metadata_json = excluded.metadata_json,
    updated_at = CURRENT_TIMESTAMP;

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES ('013', 'daily_price_observation_foundation');

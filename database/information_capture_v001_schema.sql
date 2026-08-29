PRAGMA foreign_keys = ON;

BEGIN;

CREATE TABLE IF NOT EXISTS capture_meta (
    key TEXT PRIMARY KEY,
    value TEXT NOT NULL
);
INSERT OR IGNORE INTO capture_meta(key, value)
VALUES ('contract_version', 'expectation_information_capture_v001');
INSERT OR IGNORE INTO capture_meta(key, value)
VALUES ('created_at', CURRENT_TIMESTAMP);

CREATE TABLE IF NOT EXISTS capture_runs (
    run_id TEXT PRIMARY KEY,
    started_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    input_ref TEXT,
    inserted_rows INTEGER NOT NULL DEFAULT 0,
    skipped_rows INTEGER NOT NULL DEFAULT 0,
    error_count INTEGER NOT NULL DEFAULT 0,
    metadata_json TEXT
);

-- Immutable evidence observations. Raw payload is retained verbatim-as-JSON when
-- licensing/storage permits; content_sha256 allows immutable byte/payload identity.
CREATE TABLE IF NOT EXISTS source_observations (
    observation_id TEXT PRIMARY KEY,
    source_type TEXT NOT NULL,
    source_name TEXT NOT NULL,
    source_ref TEXT,
    canonical_url TEXT,
    published_at TEXT,
    first_seen_at TEXT,
    retrieved_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    strict_pit INTEGER NOT NULL CHECK (strict_pit IN (0,1)),
    content_sha256 TEXT,
    raw_payload_json TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);
CREATE INDEX IF NOT EXISTS idx_source_obs_available
    ON source_observations(available_at);
CREATE INDEX IF NOT EXISTS idx_source_obs_source
    ON source_observations(source_type, source_name, retrieved_at);
CREATE INDEX IF NOT EXISTS idx_source_obs_hash
    ON source_observations(content_sha256);

-- A future known event is itself information. Every reschedule/cancellation is
-- appended as a new observation rather than mutating the prior state.
CREATE TABLE IF NOT EXISTS scheduled_event_observations (
    observation_id TEXT PRIMARY KEY,
    entity_key TEXT,
    asset_ticker TEXT,
    event_type TEXT NOT NULL,
    scheduled_for TEXT NOT NULL,
    event_status TEXT NOT NULL,
    available_at TEXT NOT NULL,
    strict_pit INTEGER NOT NULL CHECK (strict_pit IN (0,1)),
    source_observation_id TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(source_observation_id)
      REFERENCES source_observations(observation_id)
      ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_scheduled_entity_time
    ON scheduled_event_observations(entity_key, scheduled_for, available_at);
CREATE INDEX IF NOT EXISTS idx_scheduled_ticker_time
    ON scheduled_event_observations(asset_ticker, scheduled_for, available_at);

-- Expectations are observations, not truth. Revisions are append-only snapshots.
-- Series identity is intentionally explicit so revision features can later be
-- derived causally without overwriting what was believed earlier.
CREATE TABLE IF NOT EXISTS expectation_observations (
    observation_id TEXT PRIMARY KEY,
    entity_key TEXT NOT NULL,
    asset_ticker TEXT,
    expectation_type TEXT NOT NULL,
    metric_key TEXT NOT NULL,
    fiscal_period TEXT,
    statistic_key TEXT NOT NULL,
    value_real REAL,
    value_text TEXT,
    unit TEXT,
    currency TEXT,
    provider_as_of TEXT,
    available_at TEXT NOT NULL,
    strict_pit INTEGER NOT NULL CHECK (strict_pit IN (0,1)),
    source_observation_id TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (value_real IS NOT NULL OR value_text IS NOT NULL),
    FOREIGN KEY(source_observation_id)
      REFERENCES source_observations(observation_id)
      ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_expectation_series
    ON expectation_observations(
      entity_key, expectation_type, metric_key, fiscal_period, statistic_key,
      available_at
    );
CREATE INDEX IF NOT EXISTS idx_expectation_ticker
    ON expectation_observations(asset_ticker, available_at);

-- Reported economic facts are evidence used later to construct surprise against
-- the last legitimate pre-event expectation snapshot. They are not market
-- return outcomes and must not be back-propagated into earlier states.
CREATE TABLE IF NOT EXISTS economic_fact_observations (
    observation_id TEXT PRIMARY KEY,
    entity_key TEXT NOT NULL,
    asset_ticker TEXT,
    fact_type TEXT NOT NULL,
    metric_key TEXT NOT NULL,
    fiscal_period TEXT,
    value_real REAL,
    value_text TEXT,
    unit TEXT,
    currency TEXT,
    available_at TEXT NOT NULL,
    strict_pit INTEGER NOT NULL CHECK (strict_pit IN (0,1)),
    source_observation_id TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    CHECK (value_real IS NOT NULL OR value_text IS NOT NULL),
    FOREIGN KEY(source_observation_id)
      REFERENCES source_observations(observation_id)
      ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_fact_series
    ON economic_fact_observations(entity_key, metric_key, fiscal_period, available_at);

-- Hash manifests make capture snapshots reproducible without making them model-visible.
CREATE TABLE IF NOT EXISTS capture_manifests (
    manifest_id TEXT PRIMARY KEY,
    generated_at TEXT NOT NULL,
    cutoff_available_at TEXT,
    source_rows INTEGER NOT NULL,
    scheduled_rows INTEGER NOT NULL,
    expectation_rows INTEGER NOT NULL,
    fact_rows INTEGER NOT NULL,
    strict_pit_rows INTEGER NOT NULL,
    non_strict_pit_rows INTEGER NOT NULL,
    canonical_sha256 TEXT NOT NULL,
    metadata_json TEXT
);

COMMIT;


PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS provider_request_observations (
    request_id TEXT PRIMARY KEY,
    provider TEXT NOT NULL,
    endpoint TEXT NOT NULL,
    asset_ticker TEXT,
    requested_at TEXT NOT NULL,
    finished_at TEXT,
    status TEXT NOT NULL,
    source_observation_id TEXT,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(source_observation_id)
      REFERENCES source_observations(observation_id)
      ON DELETE RESTRICT
);
CREATE INDEX IF NOT EXISTS idx_provider_request_provider_time
    ON provider_request_observations(provider, requested_at);
CREATE INDEX IF NOT EXISTS idx_provider_request_endpoint_time
    ON provider_request_observations(endpoint, requested_at);

-- Date/window representation for scheduled events when the provider does not
-- provide a trustworthy exact timestamp. No invented clock time is allowed.
CREATE TABLE IF NOT EXISTS scheduled_event_window_observations (
    observation_id TEXT PRIMARY KEY,
    entity_key TEXT,
    asset_ticker TEXT,
    event_type TEXT NOT NULL,
    scheduled_date TEXT NOT NULL,
    daypart TEXT,
    time_precision TEXT NOT NULL,
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
CREATE INDEX IF NOT EXISTS idx_event_window_ticker_date
    ON scheduled_event_window_observations(asset_ticker, scheduled_date, available_at);

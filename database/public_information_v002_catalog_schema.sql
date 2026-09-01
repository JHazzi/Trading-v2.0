PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    applied_at_utc TEXT NOT NULL,
    schema_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS canonical_builds (
    build_id TEXT PRIMARY KEY,
    contract_version TEXT NOT NULL,
    input_fingerprint TEXT NOT NULL UNIQUE,
    config_sha256 TEXT NOT NULL,
    bars_snapshot_id TEXT NOT NULL,
    news_snapshot_id TEXT NOT NULL,
    lake_path TEXT NOT NULL,
    report_path TEXT NOT NULL,
    status TEXT NOT NULL,
    training_authorized INTEGER NOT NULL CHECK(training_authorized = 0),
    created_at_utc TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS stage_runs (
    run_id TEXT PRIMARY KEY,
    build_id TEXT NOT NULL REFERENCES canonical_builds(build_id),
    stage TEXT NOT NULL,
    status TEXT NOT NULL,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    marker_path TEXT,
    error_json TEXT
);

CREATE TABLE IF NOT EXISTS build_artifacts (
    build_id TEXT NOT NULL REFERENCES canonical_builds(build_id),
    stage TEXT NOT NULL,
    artifact_name TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    file_count INTEGER NOT NULL,
    size_bytes INTEGER NOT NULL,
    tree_sha256 TEXT NOT NULL,
    row_count INTEGER,
    status TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    PRIMARY KEY(build_id, artifact_name)
);

CREATE INDEX IF NOT EXISTS idx_stage_runs_build_stage
    ON stage_runs(build_id, stage, started_at_utc);

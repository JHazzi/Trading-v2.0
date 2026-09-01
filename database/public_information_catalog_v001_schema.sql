PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS schema_migrations (
    migration_id TEXT PRIMARY KEY,
    applied_at_utc TEXT NOT NULL,
    schema_sha256 TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_registry (
    dataset_key TEXT PRIMARY KEY,
    repo_type TEXT NOT NULL,
    repo_id TEXT NOT NULL,
    configured_revision TEXT NOT NULL,
    declared_license TEXT,
    rights_status TEXT NOT NULL,
    causal_status TEXT NOT NULL,
    model_visibility TEXT NOT NULL,
    updated_at_utc TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS dataset_snapshots (
    snapshot_id TEXT PRIMARY KEY,
    dataset_key TEXT NOT NULL REFERENCES dataset_registry(dataset_key),
    profile_name TEXT NOT NULL,
    requested_revision TEXT NOT NULL,
    resolved_revision TEXT NOT NULL,
    manifest_sha256 TEXT NOT NULL UNIQUE,
    selected_file_count INTEGER NOT NULL,
    selected_bytes INTEGER NOT NULL,
    manifest_path TEXT NOT NULL,
    first_registered_at_utc TEXT NOT NULL,
    last_verified_at_utc TEXT NOT NULL,
    UNIQUE(dataset_key, profile_name, resolved_revision, manifest_sha256)
);

CREATE TABLE IF NOT EXISTS snapshot_files (
    snapshot_id TEXT NOT NULL REFERENCES dataset_snapshots(snapshot_id),
    repo_path TEXT NOT NULL,
    size_bytes INTEGER NOT NULL,
    oid TEXT,
    lfs_sha256 TEXT,
    xet_hash TEXT,
    download_url TEXT NOT NULL,
    local_path TEXT NOT NULL,
    status TEXT NOT NULL,
    local_size_bytes INTEGER,
    local_sha256 TEXT,
    completed_at_utc TEXT,
    last_error TEXT,
    PRIMARY KEY(snapshot_id, repo_path)
);

CREATE TABLE IF NOT EXISTS intake_runs (
    run_id TEXT PRIMARY KEY,
    stage TEXT NOT NULL,
    dataset_key TEXT,
    profile_name TEXT,
    snapshot_id TEXT,
    started_at_utc TEXT NOT NULL,
    finished_at_utc TEXT,
    status TEXT NOT NULL,
    planned_bytes INTEGER NOT NULL DEFAULT 0,
    transferred_bytes INTEGER NOT NULL DEFAULT 0,
    report_path TEXT,
    error_json TEXT
);

CREATE TABLE IF NOT EXISTS audit_records (
    audit_id TEXT PRIMARY KEY,
    snapshot_id TEXT NOT NULL REFERENCES dataset_snapshots(snapshot_id),
    audit_level TEXT NOT NULL,
    status TEXT NOT NULL,
    created_at_utc TEXT NOT NULL,
    report_path TEXT NOT NULL,
    report_sha256 TEXT NOT NULL
);

CREATE INDEX IF NOT EXISTS idx_snapshot_files_status
    ON snapshot_files(snapshot_id, status);
CREATE INDEX IF NOT EXISTS idx_intake_runs_snapshot
    ON intake_runs(snapshot_id, started_at_utc);

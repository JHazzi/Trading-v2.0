PRAGMA foreign_keys = ON;

-- Market Foundation v0.1.1
-- Add explicit validation metadata for realized targets.
-- Safe to run more than once through apply_migration.py-style runners.

CREATE TABLE IF NOT EXISTS target_validation_runs (
    validation_id INTEGER PRIMARY KEY AUTOINCREMENT,
    created_at TEXT NOT NULL,
    target_version TEXT NOT NULL,
    outcomes_checked INTEGER NOT NULL DEFAULT 0,
    failures_count INTEGER NOT NULL DEFAULT 0,
    status TEXT NOT NULL,
    report_json TEXT
);

CREATE INDEX IF NOT EXISTS idx_target_validation_runs_created
    ON target_validation_runs(created_at DESC);

CREATE INDEX IF NOT EXISTS idx_realized_outcomes_target_quality
    ON realized_outcomes(target_version, data_quality, coverage_pct);

INSERT OR IGNORE INTO schema_meta(key, value)
VALUES ('market_foundation_quality_version', '0.1.1');

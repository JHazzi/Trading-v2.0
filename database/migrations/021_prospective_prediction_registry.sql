PRAGMA foreign_keys = ON;

-- Prospective Prediction Registry V001
-- Append-only separation of preregistration, model fit, prediction, outcome,
-- score and evaluation. No table depends on mutable research-result tables.

CREATE TABLE IF NOT EXISTS schema_migrations (
    version TEXT PRIMARY KEY,
    name TEXT NOT NULL,
    applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP
);

CREATE TABLE IF NOT EXISTS prospective_experiments (
    experiment_version TEXT PRIMARY KEY,
    registry_version TEXT NOT NULL,
    config_sha256 TEXT NOT NULL,
    plan_sha256 TEXT NOT NULL,
    source_checkpoint_sha256 TEXT NOT NULL,
    registered_at_utc TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status = 'PREREGISTERED'),
    plan_json TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS prospective_model_fits (
    fit_id TEXT PRIMARY KEY,
    experiment_version TEXT NOT NULL,
    model_version TEXT NOT NULL,
    fitted_at_utc TEXT NOT NULL,
    horizon_sessions INTEGER NOT NULL CHECK(horizon_sessions > 0),
    training_first_origin_day TEXT NOT NULL,
    training_last_origin_day TEXT NOT NULL,
    training_last_target_day TEXT NOT NULL,
    training_rows INTEGER NOT NULL CHECK(training_rows > 0),
    training_origin_days INTEGER NOT NULL CHECK(training_origin_days > 0),
    training_assets INTEGER NOT NULL CHECK(training_assets > 0),
    training_data_sha256 TEXT NOT NULL,
    feature_manifest_sha256 TEXT NOT NULL,
    algorithm_contract_sha256 TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    artifact_sha256 TEXT NOT NULL,
    metadata_json TEXT NOT NULL,
    UNIQUE(experiment_version),
    FOREIGN KEY(experiment_version)
      REFERENCES prospective_experiments(experiment_version)
);

CREATE TABLE IF NOT EXISTS prospective_prediction_batches (
    batch_id TEXT PRIMARY KEY,
    experiment_version TEXT NOT NULL,
    fit_id TEXT NOT NULL,
    origin_trading_day TEXT NOT NULL,
    state_time TEXT NOT NULL,
    sealed_at_utc TEXT NOT NULL,
    seal_delay_seconds REAL NOT NULL CHECK(seal_delay_seconds >= 0),
    eligible_assets INTEGER NOT NULL CHECK(eligible_assets > 0),
    predicted_assets INTEGER NOT NULL CHECK(predicted_assets > 0),
    state_snapshot_sha256 TEXT NOT NULL,
    status TEXT NOT NULL CHECK(status = 'SEALED'),
    metadata_json TEXT NOT NULL,
    UNIQUE(experiment_version, origin_trading_day),
    FOREIGN KEY(experiment_version)
      REFERENCES prospective_experiments(experiment_version),
    FOREIGN KEY(fit_id)
      REFERENCES prospective_model_fits(fit_id)
);

CREATE TABLE IF NOT EXISTS prospective_distribution_predictions (
    prediction_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    model_role TEXT NOT NULL CHECK(model_role IN ('candidate','reference')),
    model_version TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    ticker TEXT NOT NULL,
    state_id TEXT NOT NULL,
    origin_trading_day TEXT NOT NULL,
    state_time TEXT NOT NULL,
    state_point_in_time_verified INTEGER NOT NULL CHECK(state_point_in_time_verified IN (0,1)),
    q05 REAL NOT NULL,
    q25 REAL NOT NULL,
    q50 REAL NOT NULL,
    q75 REAL NOT NULL,
    q95 REAL NOT NULL,
    probability_positive REAL NOT NULL
      CHECK(probability_positive >= 0 AND probability_positive <= 1),
    feature_snapshot_json TEXT NOT NULL,
    feature_snapshot_sha256 TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    CHECK(q05 <= q25 AND q25 <= q50 AND q50 <= q75 AND q75 <= q95),
    UNIQUE(batch_id, asset_id, model_role),
    FOREIGN KEY(batch_id)
      REFERENCES prospective_prediction_batches(batch_id)
);

CREATE TABLE IF NOT EXISTS prospective_prediction_outcomes (
    outcome_id TEXT PRIMARY KEY,
    batch_id TEXT NOT NULL,
    asset_id INTEGER NOT NULL,
    origin_trading_day TEXT NOT NULL,
    target_trading_day TEXT NOT NULL,
    horizon_sessions INTEGER NOT NULL CHECK(horizon_sessions > 0),
    label_version TEXT NOT NULL,
    label_status TEXT NOT NULL,
    corporate_action_overlap INTEGER NOT NULL
      CHECK(corporate_action_overlap IN (0,1)),
    return_pct REAL,
    mfe_pct REAL,
    mae_pct REAL,
    realized_path_vol_pct REAL,
    observed_at_utc TEXT NOT NULL,
    source_label_id TEXT,
    payload_sha256 TEXT NOT NULL,
    CHECK(target_trading_day > origin_trading_day),
    UNIQUE(batch_id, asset_id),
    FOREIGN KEY(batch_id)
      REFERENCES prospective_prediction_batches(batch_id)
);

CREATE TABLE IF NOT EXISTS prospective_distribution_scores (
    prediction_id TEXT PRIMARY KEY,
    outcome_id TEXT NOT NULL,
    mean_pinball_loss REAL NOT NULL,
    pinball_q05 REAL NOT NULL,
    pinball_q25 REAL NOT NULL,
    pinball_q50 REAL NOT NULL,
    pinball_q75 REAL NOT NULL,
    pinball_q95 REAL NOT NULL,
    median_absolute_error REAL NOT NULL,
    brier_positive REAL NOT NULL,
    hit_q05 INTEGER NOT NULL CHECK(hit_q05 IN (0,1)),
    hit_q25 INTEGER NOT NULL CHECK(hit_q25 IN (0,1)),
    hit_q50 INTEGER NOT NULL CHECK(hit_q50 IN (0,1)),
    hit_q75 INTEGER NOT NULL CHECK(hit_q75 IN (0,1)),
    hit_q95 INTEGER NOT NULL CHECK(hit_q95 IN (0,1)),
    scored_at_utc TEXT NOT NULL,
    payload_sha256 TEXT NOT NULL,
    FOREIGN KEY(prediction_id)
      REFERENCES prospective_distribution_predictions(prediction_id),
    FOREIGN KEY(outcome_id)
      REFERENCES prospective_prediction_outcomes(outcome_id)
);

CREATE TABLE IF NOT EXISTS prospective_evaluation_runs (
    evaluation_id TEXT PRIMARY KEY,
    experiment_version TEXT NOT NULL,
    evaluation_version TEXT NOT NULL,
    evaluated_at_utc TEXT NOT NULL,
    cohort_policy TEXT NOT NULL,
    first_origin_day TEXT,
    last_origin_day TEXT,
    origin_days INTEGER NOT NULL CHECK(origin_days >= 0),
    usable_rows INTEGER NOT NULL CHECK(usable_rows >= 0),
    status TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
    report_json TEXT NOT NULL,
    FOREIGN KEY(experiment_version)
      REFERENCES prospective_experiments(experiment_version)
);

CREATE TABLE IF NOT EXISTS prospective_registry_audits (
    audit_id TEXT PRIMARY KEY,
    experiment_version TEXT NOT NULL,
    audited_at_utc TEXT NOT NULL,
    status TEXT NOT NULL,
    report_sha256 TEXT NOT NULL,
    report_json TEXT NOT NULL,
    FOREIGN KEY(experiment_version)
      REFERENCES prospective_experiments(experiment_version)
);

CREATE INDEX IF NOT EXISTS idx_prospective_batches_origin
    ON prospective_prediction_batches(experiment_version, origin_trading_day);
CREATE INDEX IF NOT EXISTS idx_prospective_predictions_asset_origin
    ON prospective_distribution_predictions(asset_id, origin_trading_day);
CREATE INDEX IF NOT EXISTS idx_prospective_outcomes_origin
    ON prospective_prediction_outcomes(origin_trading_day, label_status);
CREATE INDEX IF NOT EXISTS idx_prospective_evaluations_time
    ON prospective_evaluation_runs(experiment_version, evaluated_at_utc);

CREATE TRIGGER IF NOT EXISTS trg_prospective_experiments_no_update
BEFORE UPDATE ON prospective_experiments
BEGIN SELECT RAISE(ABORT, 'prospective experiments are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_prospective_experiments_no_delete
BEFORE DELETE ON prospective_experiments
BEGIN SELECT RAISE(ABORT, 'prospective experiments are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_prospective_fits_no_update
BEFORE UPDATE ON prospective_model_fits
BEGIN SELECT RAISE(ABORT, 'prospective model fits are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_prospective_fits_no_delete
BEFORE DELETE ON prospective_model_fits
BEGIN SELECT RAISE(ABORT, 'prospective model fits are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_prospective_batches_no_update
BEFORE UPDATE ON prospective_prediction_batches
BEGIN SELECT RAISE(ABORT, 'sealed prediction batches are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_prospective_batches_no_delete
BEFORE DELETE ON prospective_prediction_batches
BEGIN SELECT RAISE(ABORT, 'sealed prediction batches are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_prospective_predictions_no_update
BEFORE UPDATE ON prospective_distribution_predictions
BEGIN SELECT RAISE(ABORT, 'sealed predictions are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_prospective_predictions_no_delete
BEFORE DELETE ON prospective_distribution_predictions
BEGIN SELECT RAISE(ABORT, 'sealed predictions are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_prospective_outcomes_no_update
BEFORE UPDATE ON prospective_prediction_outcomes
BEGIN SELECT RAISE(ABORT, 'linked outcomes are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_prospective_outcomes_no_delete
BEFORE DELETE ON prospective_prediction_outcomes
BEGIN SELECT RAISE(ABORT, 'linked outcomes are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_prospective_scores_no_update
BEFORE UPDATE ON prospective_distribution_scores
BEGIN SELECT RAISE(ABORT, 'prospective scores are immutable'); END;
CREATE TRIGGER IF NOT EXISTS trg_prospective_scores_no_delete
BEFORE DELETE ON prospective_distribution_scores
BEGIN SELECT RAISE(ABORT, 'prospective scores are immutable'); END;

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES ('021', 'prospective_prediction_registry');

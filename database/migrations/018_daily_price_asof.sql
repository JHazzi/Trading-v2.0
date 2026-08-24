-- 018_daily_price_asof.sql
-- Quality-gated causal daily price selection primitives.
--
-- This layer does not calculate reactions, returns, labels or model features.
-- It exposes only versioned configuration and auditable lineage views over the
-- append-only daily price observations introduced by migration 013.

PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS daily_price_asof_configs (
    asof_contract_version TEXT NOT NULL,
    mode TEXT NOT NULL CHECK (
        mode IN (
            'system_replay',
            'historical_session_close_assumption'
        )
    ),
    cutoff_column TEXT NOT NULL CHECK (
        cutoff_column IN ('observed_at', 'available_at')
    ),
    required_quality_version TEXT NOT NULL,
    required_quality_status TEXT NOT NULL
        CHECK (required_quality_status = 'completed'),
    max_failed_checks INTEGER NOT NULL CHECK (max_failed_checks = 0),
    selection_point_in_time_verified INTEGER NOT NULL
        CHECK (selection_point_in_time_verified IN (0, 1)),
    adjusted_close_role TEXT NOT NULL
        CHECK (adjusted_close_role = 'audit_only_not_identity'),
    disclosure TEXT NOT NULL,
    configuration_json TEXT NOT NULL,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    PRIMARY KEY(asof_contract_version, mode)
);

CREATE VIEW IF NOT EXISTS daily_price_quality_eligible_retrievals_v001 AS
SELECT
    quality.quality_run_id,
    quality.batch_retrieval_id,
    quality.raw_batch_id,
    quality.source_id,
    quality.quality_version,
    COUNT(result.quality_result_id) AS check_count,
    SUM(
        CASE WHEN result.check_status = 'fail' THEN 1 ELSE 0 END
    ) AS failed_check_count,
    SUM(
        CASE WHEN result.check_status = 'warn' THEN 1 ELSE 0 END
    ) AS warning_check_count
FROM price_quality_runs AS quality
JOIN price_quality_results AS result
  ON result.quality_run_id = quality.quality_run_id
 AND result.raw_batch_id = quality.raw_batch_id
WHERE quality.status = 'completed'
GROUP BY
    quality.quality_run_id,
    quality.batch_retrieval_id,
    quality.raw_batch_id,
    quality.source_id,
    quality.quality_version
HAVING COUNT(result.quality_result_id) > 0
   AND SUM(
       CASE WHEN result.check_status = 'fail' THEN 1 ELSE 0 END
   ) = 0;

CREATE VIEW IF NOT EXISTS daily_price_quality_gated_observations_v001 AS
SELECT
    observation.price_observation_id,
    observation.price_bar_version_id,
    observation.raw_batch_id,
    observation.batch_retrieval_id,
    observation.source_id,
    observation.asset_id,
    observation.provider_symbol,
    observation.interval,
    observation.trading_day,
    observation.provider_row_number,
    observation.observed_at,
    observation.observed_adjusted_close,
    observation.available_at,
    observation.availability_basis,
    observation.point_in_time_verified
        AS observation_point_in_time_verified,
    observation.observation_kind,
    observation.observation_sequence,
    observation.state_revision_number,
    observation.previous_observation_id,
    version.exchange,
    version.calendar_name,
    version.bar_start_utc,
    version.bar_end_utc,
    version.open,
    version.high,
    version.low,
    version.close,
    version.volume,
    version.adjusted_close AS first_observed_adjusted_close,
    version.bar_content_sha256,
    version.normalized_bar_json,
    eligible.quality_run_id,
    eligible.quality_version,
    eligible.check_count,
    eligible.failed_check_count,
    eligible.warning_check_count
FROM price_bar_observations AS observation
JOIN price_bar_versions AS version
  ON version.price_bar_version_id = observation.price_bar_version_id
 AND version.source_id = observation.source_id
 AND version.asset_id = observation.asset_id
 AND version.interval = observation.interval
 AND version.trading_day = observation.trading_day
JOIN daily_price_quality_eligible_retrievals_v001 AS eligible
  ON eligible.batch_retrieval_id = observation.batch_retrieval_id
 AND eligible.raw_batch_id = observation.raw_batch_id
 AND eligible.source_id = observation.source_id;

INSERT OR IGNORE INTO daily_price_asof_configs(
    asof_contract_version,
    mode,
    cutoff_column,
    required_quality_version,
    required_quality_status,
    max_failed_checks,
    selection_point_in_time_verified,
    adjusted_close_role,
    disclosure,
    configuration_json
)
VALUES
(
    'daily_price_asof_v1',
    'system_replay',
    'observed_at',
    'daily_price_quality_v2',
    'completed',
    0,
    1,
    'audit_only_not_identity',
    'Exact replay of what this system had observed by as_of; provider history is unavailable before its retrieval.',
    '{"cutoff":"observed_at","quality_gate":{"failed_checks":0,"status":"completed","version":"daily_price_quality_v2"},"state_order":"observation_sequence","pit_scope":"system_state"}'
),
(
    'daily_price_asof_v1',
    'historical_session_close_assumption',
    'available_at',
    'daily_price_quality_v2',
    'completed',
    0,
    0,
    'audit_only_not_identity',
    'Initial backfill uses an inferred session-close availability and is not verified revision-free point-in-time history.',
    '{"cutoff":"available_at","quality_gate":{"failed_checks":0,"status":"completed","version":"daily_price_quality_v2"},"state_order":"observation_sequence","pit_scope":"historical_assumption"}'
);

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES ('018', 'daily_price_asof');

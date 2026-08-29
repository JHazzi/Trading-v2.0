-- 023_daily_price_first_quality_eligible.sql
-- Preserve failed observations while treating the first quality-eligible
-- provider version as the initial non-PIT session-close reconstruction.

PRAGMA foreign_keys = ON;

CREATE VIEW IF NOT EXISTS daily_price_quality_gated_observations_v002 AS
WITH ranked AS (
    SELECT
        gated.*,
        ROW_NUMBER() OVER (
            PARTITION BY gated.source_id, gated.asset_id,
                         gated.interval, gated.trading_day
            ORDER BY gated.observation_sequence ASC,
                     julianday(gated.observed_at) ASC,
                     gated.price_observation_id ASC
        ) AS quality_eligible_rank
    FROM daily_price_quality_gated_observations_v001 AS gated
)
SELECT
    ranked.*,
    CASE
        WHEN ranked.quality_eligible_rank = 1
        THEN ranked.bar_end_utc
        ELSE ranked.available_at
    END AS causal_available_at,
    CASE
        WHEN ranked.quality_eligible_rank = 1
        THEN 'first_quality_eligible_session_close_assumption'
        ELSE ranked.availability_basis
    END AS causal_availability_basis
FROM ranked;

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
VALUES (
    'daily_price_asof_v2',
    'historical_session_close_assumption',
    'available_at',
    'daily_price_quality_v2',
    'completed',
    0,
    0,
    'audit_only_not_identity',
    'PIT=0 reconstruction: failed retrievals remain in lineage; the first quality-eligible observation is the initial session-close assumption. Actual observed_at is preserved.',
    '{"cutoff_view_column":"causal_available_at","failed_observations_preserved":true,"first_quality_eligible_policy":"session_close_assumption","quality_gate":{"failed_checks":0,"status":"completed","version":"daily_price_quality_v2"},"pit_scope":"historical_reconstruction"}'
);

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES ('023', 'daily_price_first_quality_eligible');
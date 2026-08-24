-- Safety/repair migration: remove duplicate logical outcomes while keeping the newest row.
-- Logical identity is (asset_id, origin_time, horizon_seconds).
DELETE FROM realized_outcomes
WHERE outcome_id NOT IN (
    SELECT MAX(outcome_id)
    FROM realized_outcomes
    GROUP BY asset_id, origin_time, horizon_seconds
);

CREATE UNIQUE INDEX IF NOT EXISTS ux_realized_outcomes_logical
ON realized_outcomes(asset_id, origin_time, horizon_seconds);

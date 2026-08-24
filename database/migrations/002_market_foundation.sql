PRAGMA foreign_keys = ON;

-- Market Foundation v0.1
-- Adds data-quality metadata to realized outcomes without changing
-- the historical definition of return/MFE/MAE.

ALTER TABLE realized_outcomes ADD COLUMN observed_bars INTEGER;
ALTER TABLE realized_outcomes ADD COLUMN expected_bars INTEGER;
ALTER TABLE realized_outcomes ADD COLUMN coverage_pct REAL;
ALTER TABLE realized_outcomes ADD COLUMN max_gap_seconds REAL;
ALTER TABLE realized_outcomes ADD COLUMN session_count INTEGER;
ALTER TABLE realized_outcomes ADD COLUMN data_quality TEXT;
ALTER TABLE realized_outcomes ADD COLUMN quality_version TEXT;

CREATE INDEX IF NOT EXISTS idx_outcomes_quality
    ON realized_outcomes(asset_id, horizon_seconds, coverage_pct);

INSERT OR IGNORE INTO schema_meta(key, value)
VALUES ('market_foundation_version', '0.1.0');

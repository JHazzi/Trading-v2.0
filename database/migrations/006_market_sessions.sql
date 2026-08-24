BEGIN;

CREATE TABLE IF NOT EXISTS market_sessions (
    session_id TEXT PRIMARY KEY,
    trading_day TEXT NOT NULL,
    exchange TEXT NOT NULL,
    session_type TEXT NOT NULL,
    open_time TEXT NOT NULL,
    close_time TEXT NOT NULL,
    UNIQUE(exchange, trading_day, session_type)
);

CREATE INDEX IF NOT EXISTS idx_market_sessions_day
ON market_sessions(exchange, trading_day);

ALTER TABLE price_bars ADD COLUMN session_id TEXT;
ALTER TABLE price_bars ADD COLUMN trading_day TEXT;

CREATE INDEX IF NOT EXISTS idx_price_bars_session
ON price_bars(asset_id, session_id, timestamp);

ALTER TABLE realized_outcomes ADD COLUMN horizon_value INTEGER;
ALTER TABLE realized_outcomes ADD COLUMN horizon_unit TEXT;
ALTER TABLE realized_outcomes ADD COLUMN horizon_scope TEXT;
ALTER TABLE realized_outcomes ADD COLUMN origin_session_id TEXT;
ALTER TABLE realized_outcomes ADD COLUMN target_session_id TEXT;
ALTER TABLE realized_outcomes ADD COLUMN session_type TEXT;

CREATE INDEX IF NOT EXISTS idx_outcomes_scope
ON realized_outcomes(horizon_scope, horizon_value);

COMMIT;

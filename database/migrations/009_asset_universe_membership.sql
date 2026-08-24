CREATE TABLE IF NOT EXISTS asset_universe_membership (
    membership_id INTEGER PRIMARY KEY AUTOINCREMENT,
    asset_id INTEGER NOT NULL,
    universe TEXT NOT NULL,
    valid_from TEXT NOT NULL,
    valid_to TEXT,
    source TEXT NOT NULL,
    confidence REAL NOT NULL DEFAULT 1.0,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,

    UNIQUE(asset_id, universe, valid_from),

    FOREIGN KEY(asset_id)
        REFERENCES assets(asset_id)
        ON DELETE CASCADE
);

CREATE INDEX IF NOT EXISTS idx_asset_universe_membership_lookup
ON asset_universe_membership(universe, valid_from, valid_to, asset_id);

CREATE INDEX IF NOT EXISTS idx_asset_universe_membership_asset
ON asset_universe_membership(asset_id, universe);

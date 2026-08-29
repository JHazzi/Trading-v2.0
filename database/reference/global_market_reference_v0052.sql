PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS global_reference_sources(
  source_id TEXT PRIMARY KEY,
  description TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS raw_global_reference_batches(
  raw_batch_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  source_url TEXT NOT NULL,
  retrieved_at TEXT NOT NULL,
  raw_sha256 TEXT NOT NULL,
  storage_path TEXT NOT NULL,
  byte_length INTEGER NOT NULL,
  row_count INTEGER NOT NULL,
  parser_version TEXT NOT NULL
);

CREATE TABLE IF NOT EXISTS global_reference_versions(
  version_id TEXT PRIMARY KEY,
  source_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  trading_day TEXT NOT NULL,
  open REAL NOT NULL,
  high REAL NOT NULL,
  low REAL NOT NULL,
  close REAL NOT NULL,
  content_sha256 TEXT NOT NULL,
  normalized_json TEXT NOT NULL,
  first_raw_batch_id TEXT NOT NULL,
  UNIQUE(source_id,symbol,trading_day,content_sha256)
);

CREATE TABLE IF NOT EXISTS global_reference_observations(
  observation_id TEXT PRIMARY KEY,
  version_id TEXT NOT NULL,
  raw_batch_id TEXT NOT NULL,
  source_id TEXT NOT NULL,
  symbol TEXT NOT NULL,
  trading_day TEXT NOT NULL,
  observed_at TEXT NOT NULL,
  available_at TEXT NOT NULL,
  availability_basis TEXT NOT NULL,
  point_in_time_verified INTEGER NOT NULL CHECK(point_in_time_verified IN (0,1)),
  observation_kind TEXT NOT NULL,
  observation_sequence INTEGER NOT NULL,
  previous_observation_id TEXT,
  UNIQUE(source_id,symbol,trading_day,observation_sequence)
);

CREATE INDEX IF NOT EXISTS idx_gro_symbol_day
  ON global_reference_observations(source_id,symbol,trading_day);

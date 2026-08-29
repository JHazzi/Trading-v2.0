
PRAGMA foreign_keys = ON;

CREATE TABLE IF NOT EXISTS news_document_observations (
    observation_id TEXT PRIMARY KEY,
    source_observation_id TEXT NOT NULL,
    provider_document_id TEXT,
    canonical_url TEXT,
    title TEXT NOT NULL,
    summary_text TEXT,
    publisher_name TEXT,
    publisher_domain TEXT,
    language TEXT,
    published_at TEXT,
    first_seen_at TEXT NOT NULL,
    available_at TEXT NOT NULL,
    strict_pit INTEGER NOT NULL CHECK (strict_pit IN (0,1)),
    document_sha256 TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(source_observation_id)
      REFERENCES source_observations(observation_id)
      ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_news_document_available
    ON news_document_observations(available_at);

CREATE INDEX IF NOT EXISTS idx_news_document_url
    ON news_document_observations(canonical_url);

CREATE INDEX IF NOT EXISTS idx_news_document_hash
    ON news_document_observations(document_sha256);

CREATE TABLE IF NOT EXISTS news_asset_annotations (
    observation_id TEXT PRIMARY KEY,
    news_document_id TEXT NOT NULL,
    asset_ticker TEXT NOT NULL,
    provider_relevance_score REAL,
    provider_sentiment_score REAL,
    provider_sentiment_label TEXT,
    available_at TEXT NOT NULL,
    strict_pit INTEGER NOT NULL CHECK (strict_pit IN (0,1)),
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(news_document_id)
      REFERENCES news_document_observations(observation_id)
      ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_news_asset_ticker_time
    ON news_asset_annotations(asset_ticker, available_at);

CREATE TABLE IF NOT EXISTS news_topic_annotations (
    observation_id TEXT PRIMARY KEY,
    news_document_id TEXT NOT NULL,
    topic_key TEXT NOT NULL,
    provider_relevance_score REAL,
    available_at TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(news_document_id)
      REFERENCES news_document_observations(observation_id)
      ON DELETE RESTRICT
);

CREATE INDEX IF NOT EXISTS idx_news_topic_time
    ON news_topic_annotations(topic_key, available_at);

-- Story identity is intentionally not automated yet. This table is a future
-- review/learned-clustering boundary. Documents are evidence; stories/events are
-- separate objects.
CREATE TABLE IF NOT EXISTS news_story_cluster_candidates (
    candidate_id TEXT PRIMARY KEY,
    cluster_version TEXT NOT NULL,
    news_document_id TEXT NOT NULL,
    candidate_cluster_key TEXT NOT NULL,
    candidate_similarity REAL,
    candidate_status TEXT NOT NULL,
    metadata_json TEXT,
    created_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
    FOREIGN KEY(news_document_id)
      REFERENCES news_document_observations(observation_id)
      ON DELETE RESTRICT
);

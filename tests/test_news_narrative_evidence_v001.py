from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from ingestion.news.alphavantage_news_evidence_v001 import canonical_url, normalize_article
from research.news.news_evidence_foundation_v001 import apply_schema, insert_normalized_feed


def make_base(db: Path):
    c=sqlite3.connect(db)
    c.execute("""CREATE TABLE source_observations(
        observation_id TEXT PRIMARY KEY,
        source_type TEXT, source_name TEXT, source_ref TEXT, canonical_url TEXT,
        published_at TEXT, first_seen_at TEXT, retrieved_at TEXT, available_at TEXT,
        strict_pit INTEGER, content_sha256 TEXT, raw_payload_json TEXT, metadata_json TEXT
    )""")
    c.execute("""INSERT INTO source_observations VALUES
        ('src','news_api_response','Alpha Vantage','x',NULL,NULL,
         '2026-08-28T01:00:00+00:00','2026-08-28T01:00:00+00:00',
         '2026-08-28T01:00:00+00:00',1,'h','{}','{}')""")
    c.commit(); c.close()


def test_tracking_params_removed():
    assert canonical_url("https://x.com/a?utm_source=z&id=2#frag") == "https://x.com/a?id=2"


def test_available_at_is_retrieval_not_provider_publication():
    a=normalize_article({
        "title":"Company reports news",
        "url":"https://example.com/a",
        "time_published":"20260827T230000",
        "ticker_sentiment":[{"ticker":"AAPL","relevance_score":"0.9","ticker_sentiment_score":"0.2",
                             "ticker_sentiment_label":"Somewhat-Bullish"}],
        "topics":[{"topic":"Technology","relevance_score":"0.8"}],
    },"src","2026-08-28T01:00:00+00:00")
    assert a["document"]["published_at"]=="2026-08-27T23:00:00+00:00"
    assert a["document"]["available_at"]=="2026-08-28T01:00:00+00:00"
    assert a["assets"][0]["metadata_json"]["provider_annotation_only"] is True


def test_quarter_not_relevant_but_news_dedupe_is_idempotent(tmp_path):
    db=tmp_path/"x.db"; make_base(db)
    schema=Path(__file__).resolve().parents[1]/"database/news_narrative_evidence_v001_additive.sql"
    apply_schema(db,schema)
    feed=[{
      "title":"Same story",
      "url":"https://example.com/a?utm_source=a",
      "time_published":"20260827T230000",
      "source":"Example",
      "ticker_sentiment":[{"ticker":"MSFT","relevance_score":"0.8",
                           "ticker_sentiment_score":"-0.1","ticker_sentiment_label":"Neutral"}],
      "topics":[]
    }]
    first=insert_normalized_feed(db,source_observation_id="src",feed=feed,
                                 retrieved_at="2026-08-28T01:00:00+00:00")
    second=insert_normalized_feed(db,source_observation_id="src",feed=feed,
                                  retrieved_at="2026-08-28T01:00:00+00:00")
    assert first["documents_inserted"]==1
    assert second["documents_inserted"]==0
    c=sqlite3.connect(db)
    assert c.execute("SELECT COUNT(*) FROM news_document_observations").fetchone()[0]==1
    assert c.execute("SELECT COUNT(*) FROM news_asset_annotations").fetchone()[0]==1

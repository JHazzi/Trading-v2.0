from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

from ingestion.news.alphavantage_news_evidence_v001 import normalize_article

CONTRACT_VERSION = "news_narrative_evidence_v001"


def conn(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    c.execute("PRAGMA foreign_keys = ON")
    return c


def apply_schema(db_path: Path, schema_path: Path) -> None:
    with conn(db_path) as c:
        c.executescript(schema_path.read_text(encoding="utf-8"))


def stable_id(prefix: str, payload: dict[str, Any]) -> str:
    blob = json.dumps(payload, sort_keys=True, separators=(",", ":"), ensure_ascii=False)
    return prefix + "-" + hashlib.sha256(blob.encode("utf-8")).hexdigest()[:32]


def insert_source_snapshot(
    db_path: Path,
    *,
    raw_payload: bytes,
    retrieved_at: str,
    source_ref: str,
    canonical_url: str | None = None,
) -> tuple[str, bool]:
    content_sha = hashlib.sha256(raw_payload).hexdigest()
    payload = {
        "source_name": "Alpha Vantage",
        "source_ref": source_ref,
        "retrieved_at": retrieved_at,
        "content_sha256": content_sha,
    }
    obs_id = stable_id("source_observation", payload)
    raw_json = raw_payload.decode("utf-8", errors="replace")
    with conn(db_path) as c:
        before = c.total_changes
        c.execute(
            """INSERT OR IGNORE INTO source_observations
               (observation_id,source_type,source_name,source_ref,canonical_url,
                published_at,first_seen_at,retrieved_at,available_at,strict_pit,
                content_sha256,raw_payload_json,metadata_json)
               VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (
                obs_id, "news_api_response", "Alpha Vantage", source_ref, canonical_url,
                None, retrieved_at, retrieved_at, retrieved_at, 1,
                content_sha, raw_json,
                json.dumps({"contract_version": CONTRACT_VERSION}, sort_keys=True),
            ),
        )
        inserted = c.total_changes > before
    return obs_id, inserted


def insert_normalized_feed(
    db_path: Path,
    *,
    source_observation_id: str,
    feed: list[dict[str, Any]],
    retrieved_at: str,
) -> dict[str, int]:
    docs = assets = topics = skipped = 0
    with conn(db_path) as c:
        for article in feed:
            try:
                item = normalize_article(article, source_observation_id, retrieved_at)
            except ValueError:
                skipped += 1
                continue
            d = item["document"]
            before = c.total_changes
            c.execute(
                """INSERT OR IGNORE INTO news_document_observations
                   (observation_id,source_observation_id,provider_document_id,canonical_url,title,
                    summary_text,publisher_name,publisher_domain,language,published_at,first_seen_at,
                    available_at,strict_pit,document_sha256,metadata_json)
                   VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
                (
                    d["observation_id"], d["source_observation_id"], d["provider_document_id"],
                    d["canonical_url"], d["title"], d["summary_text"], d["publisher_name"],
                    d["publisher_domain"], d["language"], d["published_at"], d["first_seen_at"],
                    d["available_at"], d["strict_pit"], d["document_sha256"],
                    json.dumps(d["metadata_json"], sort_keys=True),
                ),
            )
            docs += int(c.total_changes > before)

            for a in item["assets"]:
                before = c.total_changes
                c.execute(
                    """INSERT OR IGNORE INTO news_asset_annotations
                       (observation_id,news_document_id,asset_ticker,provider_relevance_score,
                        provider_sentiment_score,provider_sentiment_label,available_at,strict_pit,metadata_json)
                       VALUES (?,?,?,?,?,?,?,?,?)""",
                    (
                        a["observation_id"], a["news_document_id"], a["asset_ticker"],
                        a["provider_relevance_score"], a["provider_sentiment_score"],
                        a["provider_sentiment_label"], a["available_at"], a["strict_pit"],
                        json.dumps(a["metadata_json"], sort_keys=True),
                    ),
                )
                assets += int(c.total_changes > before)

            for t in item["topics"]:
                before = c.total_changes
                c.execute(
                    """INSERT OR IGNORE INTO news_topic_annotations
                       (observation_id,news_document_id,topic_key,provider_relevance_score,
                        available_at,metadata_json)
                       VALUES (?,?,?,?,?,?)""",
                    (
                        t["observation_id"], t["news_document_id"], t["topic_key"],
                        t["provider_relevance_score"], t["available_at"],
                        json.dumps(t["metadata_json"], sort_keys=True),
                    ),
                )
                topics += int(c.total_changes > before)

    return {
        "documents_inserted": docs,
        "asset_annotations_inserted": assets,
        "topic_annotations_inserted": topics,
        "articles_skipped_missing_title": skipped,
    }


def audit(db_path: Path) -> dict[str, Any]:
    with conn(db_path) as c:
        counts = {}
        for table in ("news_document_observations","news_asset_annotations","news_topic_annotations"):
            counts[table] = int(c.execute(f"SELECT COUNT(*) FROM {table}").fetchone()[0])

        orphan_assets = int(c.execute(
            """SELECT COUNT(*) FROM news_asset_annotations a
               LEFT JOIN news_document_observations d ON a.news_document_id=d.observation_id
               WHERE d.observation_id IS NULL"""
        ).fetchone()[0])
        orphan_topics = int(c.execute(
            """SELECT COUNT(*) FROM news_topic_annotations a
               LEFT JOIN news_document_observations d ON a.news_document_id=d.observation_id
               WHERE d.observation_id IS NULL"""
        ).fetchone()[0])
        invalid_time = int(c.execute(
            """SELECT COUNT(*) FROM news_document_observations
               WHERE strict_pit=1 AND datetime(available_at) < datetime(first_seen_at)"""
        ).fetchone()[0])
        duplicate_urls = int(c.execute(
            """SELECT COALESCE(SUM(n-1),0) FROM (
                 SELECT canonical_url, COUNT(*) n
                 FROM news_document_observations
                 WHERE canonical_url IS NOT NULL
                 GROUP BY canonical_url HAVING COUNT(*)>1
               )"""
        ).fetchone()[0])
        duplicate_hashes = int(c.execute(
            """SELECT COALESCE(SUM(n-1),0) FROM (
                 SELECT document_sha256, COUNT(*) n
                 FROM news_document_observations
                 GROUP BY document_sha256 HAVING COUNT(*)>1
               )"""
        ).fetchone()[0])
        provider_sentiment_annotations = int(c.execute(
            "SELECT COUNT(*) FROM news_asset_annotations WHERE provider_sentiment_score IS NOT NULL"
        ).fetchone()[0])

    status = "PASS" if not (orphan_assets or orphan_topics or invalid_time) else "FAIL"
    return {
        "status": status,
        "contract_version": CONTRACT_VERSION,
        "counts": counts,
        "orphan_asset_annotations": orphan_assets,
        "orphan_topic_annotations": orphan_topics,
        "invalid_strict_pit_time_rows": invalid_time,
        "duplicate_canonical_url_rows": duplicate_urls,
        "duplicate_document_hash_rows": duplicate_hashes,
        "provider_sentiment_annotations": provider_sentiment_annotations,
        "feature_visibility": "BLOCKED",
        "story_clustering_visibility": "BLOCKED_PENDING_SEPARATE_VERSION",
        "interpretation": (
            "Provider sentiment/relevance fields are preserved as annotations, not target labels or "
            "economic impact. Documents remain evidence; repeated coverage is not counted as repeated shocks."
        ),
    }

import sqlite3
from pathlib import Path

from research.audits.information_archaeology_v001 import inspect_db, semantic_summary


def make_db(path: Path):
    c = sqlite3.connect(path)
    c.executescript("""
    PRAGMA foreign_keys=ON;
    CREATE TABLE raw_source_documents(
      raw_document_id TEXT PRIMARY KEY,
      source_name TEXT,
      retrieved_at TEXT,
      available_at TEXT,
      strict_pit INTEGER
    );
    CREATE TABLE news_documents(
      document_id TEXT PRIMARY KEY,
      raw_document_id TEXT,
      title TEXT,
      published_at TEXT,
      available_at TEXT,
      FOREIGN KEY(raw_document_id) REFERENCES raw_source_documents(raw_document_id)
    );
    CREATE TABLE news_assets(
      id INTEGER PRIMARY KEY,
      document_id TEXT,
      ticker TEXT,
      FOREIGN KEY(document_id) REFERENCES news_documents(document_id)
    );
    CREATE TABLE event_clusters(
      cluster_id TEXT PRIMARY KEY,
      first_seen_at TEXT,
      available_at TEXT
    );
    CREATE TABLE event_reaction_outcomes(
      id INTEGER PRIMARY KEY,
      cluster_id TEXT,
      observed_at TEXT
    );
    CREATE TABLE relation_evidence(
      id INTEGER PRIMARY KEY,
      source_entity_id TEXT,
      target_entity_id TEXT,
      available_at TEXT
    );
    """)
    c.execute("INSERT INTO raw_source_documents VALUES ('r1','Reuters','2026-01-01T10:00:00Z','2026-01-01T10:00:00Z',1)")
    c.execute("INSERT INTO news_documents VALUES ('d1','r1','A','2026-01-01T09:59:00Z','2026-01-01T10:00:00Z')")
    c.execute("INSERT INTO news_assets(document_id,ticker) VALUES ('d1','AAPL')")
    c.execute("INSERT INTO event_clusters VALUES ('c1','2026-01-01T10:00:00Z','2026-01-01T10:00:00Z')")
    c.execute("INSERT INTO event_reaction_outcomes(cluster_id,observed_at) VALUES ('c1','2026-01-02T00:00:00Z')")
    c.execute("INSERT INTO relation_evidence(source_entity_id,target_entity_id,available_at) VALUES ('a','b','2026-01-01T00:00:00Z')")
    c.commit(); c.close()


def test_read_only_inspection(tmp_path):
    db = tmp_path / "x.db"
    make_db(db)
    out = inspect_db(db, ["raw_source_documents","news_documents","news_assets","event_clusters","event_reaction_outcomes","relation_evidence"])
    assert out["table_info"]["news_documents"]["rows"] == 1
    assert "available_at" in out["table_info"]["news_documents"]["time_coverage"]


def test_foreign_keys_have_no_orphans(tmp_path):
    db = tmp_path / "x.db"
    make_db(db)
    out = inspect_db(db, ["raw_source_documents","news_documents","news_assets"])
    assert all(e["orphan_rows"] == 0 for e in out["foreign_key_edges"])


def test_semantic_summary_detects_populated_layers(tmp_path):
    db = tmp_path / "x.db"
    make_db(db)
    primary = inspect_db(db, ["raw_source_documents","news_documents","news_assets","event_clusters","event_reaction_outcomes","relation_evidence"])
    s = semantic_summary(primary, [])
    assert s["news_documents_rows"] == 1
    assert s["event_cluster_rows"] == 1
    assert s["event_reaction_or_label_rows"] == 1
    assert s["primary_graph_rows"] == 1

import sqlite3
from pathlib import Path

from research.audits.news_event_lineage_review_v001 import (
    ro_connect, same_name_join_candidates, candidate_time_lineage, scan_code
)


def make_db(path: Path):
    c=sqlite3.connect(path)
    c.executescript("""
    CREATE TABLE raw_source_documents(
      source_document_id TEXT PRIMARY KEY,
      retrieved_at TEXT,
      available_at TEXT,
      source_name TEXT
    );
    CREATE TABLE news_documents(
      document_id TEXT PRIMARY KEY,
      source_document_id TEXT,
      published_at TEXT,
      title TEXT,
      FOREIGN KEY(source_document_id) REFERENCES raw_source_documents(source_document_id)
    );
    """)
    c.execute("INSERT INTO raw_source_documents VALUES ('r1','2026-01-01T10:00:00Z','2026-01-01T10:00:00Z','Reuters')")
    c.execute("INSERT INTO news_documents VALUES ('d1','r1','2026-01-01T09:59:00Z','x')")
    c.commit(); c.close()


def test_join_and_time_lineage(tmp_path):
    db=tmp_path/"x.db"; make_db(db)
    c=ro_connect(db)
    joins=same_name_join_candidates(c,"news_documents","raw_source_documents")
    assert joins[0]["column"]=="source_document_id"
    assert joins[0]["matched_source_rows"]==1
    lin=candidate_time_lineage(c,"news_documents","raw_source_documents","source_document_id")
    assert lin["coverage_fraction"]==1.0
    assert lin["parent_time_field"]=="available_at"
    c.close()


def test_read_only_does_not_create_missing_db(tmp_path):
    p=tmp_path/"missing.db"
    try:
        ro_connect(p)
    except FileNotFoundError:
        pass
    assert not p.exists()


def test_code_scanner_finds_writer_reference(tmp_path):
    (tmp_path/"pipeline").mkdir()
    (tmp_path/"pipeline"/"x.py").write_text("INSERT INTO event_reaction_outcomes VALUES (...)")
    refs=scan_code(tmp_path,["event_reaction_outcomes"])
    assert refs["event_reaction_outcomes"][0]["file"]=="pipeline/x.py"

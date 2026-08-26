from __future__ import annotations

import sqlite3
from pathlib import Path

from evaluation.events.event_graph_relation_source_audit_v001 import (
    audit,
)


def make_db(tmp_path: Path) -> Path:
    db = tmp_path / "test.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        CREATE TABLE schema_migrations(
          version TEXT PRIMARY KEY,
          name TEXT NOT NULL
        );
        INSERT INTO schema_migrations VALUES ('019','event_brain_v001');
        INSERT INTO schema_migrations VALUES ('020','event_graph_brain_foundation');

        CREATE TABLE assets(
          asset_id INTEGER PRIMARY KEY,
          ticker TEXT,
          asset_type TEXT,
          active INTEGER
        );
        INSERT INTO assets VALUES (1,'AAA','equity',1);

        CREATE TABLE entities(
          entity_id INTEGER PRIMARY KEY,
          entity_type TEXT,
          canonical_name TEXT
        );
        CREATE TABLE asset_entities(
          asset_id INTEGER PRIMARY KEY,
          entity_id INTEGER
        );

        CREATE TABLE sec_source_documents(
          source_document_id TEXT PRIMARY KEY,
          asset_id INTEGER,
          accession_number TEXT,
          accepted_at TEXT,
          retrieved_at TEXT,
          raw_text TEXT,
          source_url TEXT,
          version_id TEXT
        );
        INSERT INTO sec_source_documents VALUES
          ('d1',1,'0001','2020-01-01T10:00:00Z',
           '2020-01-01T10:01:00Z','Supplier: Beta Inc',
           'https://sec.example/d1','v1');

        CREATE TABLE normalized_event_state_snapshots(
          event_state_id TEXT PRIMARY KEY,
          event_id TEXT,
          asset_id INTEGER,
          state_time TEXT,
          feature_version TEXT
        );
        INSERT INTO normalized_event_state_snapshots VALUES
          ('s1','e1',1,'2020-01-01T10:00:00Z','event_state_v002');

        CREATE TABLE event_entity_links_v001(
          event_entity_link_id TEXT PRIMARY KEY,
          event_id TEXT,
          entity_id INTEGER,
          asset_id INTEGER,
          first_available_at TEXT
        );
        """)
        conn.commit()
    return db


def test_audit_is_read_only_and_finds_strong_source(tmp_path):
    db = make_db(tmp_path)
    before = db.read_bytes()
    out = audit(db)
    after = db.read_bytes()

    assert out["status"] == "PASS"
    assert out["read_only"] is True
    assert before == after
    names = [
        x["table"] for x in out["relation_source_candidates"]
    ]
    assert "sec_source_documents" in names
    sec = next(
        x for x in out["relation_source_candidates"]
        if x["table"] == "sec_source_documents"
    )
    assert sec["relation_source_readiness"]["tier"] == "A"
    assert sec["relation_source_readiness"][
        "full_text_candidate"
    ] is True
    assert sec["relation_source_readiness"][
        "source_reference_candidate"
    ] is True
    assert sec["relation_source_readiness"][
        "entity_resolution_candidate"
    ] is True


def test_migration_history_reports_local_019_and_foundation_020(tmp_path):
    db = make_db(tmp_path)
    out = audit(db)
    history = {
        x["version"]: x["name"]
        for x in out["migration_history_tail"]
    }
    assert history["019"] == "event_brain_v001"
    assert history["020"] == "event_graph_brain_foundation"


def test_no_relation_extraction_or_training_claim(tmp_path):
    db = make_db(tmp_path)
    out = audit(db)
    contract = out["scientific_contract"]
    assert contract["database_mutated"] is False
    assert contract["relations_extracted"] is False
    assert contract["models_trained"] is False
    assert contract["future_evidence_not_allowed"] is True


def test_event_table_is_in_core_inventory(tmp_path):
    db = make_db(tmp_path)
    out = audit(db)
    assert "normalized_event_state_snapshots" in out["core_inventory"]


def test_source_without_text_is_ranked_lower(tmp_path):
    db = make_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("""
        CREATE TABLE sparse_filing_index(
          filing_id TEXT PRIMARY KEY,
          ticker TEXT,
          filed_at TEXT
        )
        """)
        conn.execute(
            "INSERT INTO sparse_filing_index VALUES ('f1','AAA','2020-01-01')"
        )
        conn.commit()
    out = audit(db)
    src = {
        x["table"]: x
        for x in out["relation_source_candidates"]
    }
    assert (
        src["sec_source_documents"]["relation_source_readiness"][
            "readiness_score"
        ]
        >
        src["sparse_filing_index"]["relation_source_readiness"][
            "readiness_score"
        ]
    )


def test_audit_does_not_require_public_repo_schema(tmp_path):
    db = make_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute("""
        CREATE TABLE custom_local_event_evidence(
          id TEXT PRIMARY KEY,
          ticker TEXT,
          available_at TEXT,
          content TEXT,
          source_ref TEXT,
          revision_id TEXT
        )
        """)
        conn.execute("""
        INSERT INTO custom_local_event_evidence
        VALUES ('x','AAA','2021-01-01T00:00:00Z',
                'relationship evidence','ref-x','r1')
        """)
        conn.commit()
    out = audit(db)
    names = {
        x["table"] for x in out["relation_source_candidates"]
    }
    assert "custom_local_event_evidence" in names


def test_accepted_at_is_clock_candidate_not_pit_claim(tmp_path):
    db = make_db(tmp_path)
    out = audit(db)
    sec = next(
        x for x in out["relation_source_candidates"]
        if x["table"] == "sec_source_documents"
    )
    readiness = sec["relation_source_readiness"]
    assert readiness["causal_time_candidate"] is True
    assert readiness["explicit_availability_candidate"] is False
    assert readiness["pit_verified_by_audit"] is False

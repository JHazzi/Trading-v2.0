from __future__ import annotations

import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

from knowledge.relations.sec_relation_corpus_v001 import (
    chunks,
    classify_document,
    decode_payload,
    normalized_text,
    plan,
)


ROOT = Path(__file__).resolve().parents[1]


def cfg():
    return json.loads(
        (ROOT / "config/event_graph_sec_relation_corpus_v001.json")
        .read_text(encoding="utf-8")
    )


def test_primary_and_relation_exhibits_are_selected():
    c = cfg()
    assert classify_document(
        form="10-K", is_primary=1, document_type="10-K",
        description="Annual report", cfg=c
    ) == "primary_narrative"
    assert classify_document(
        form="10-K", is_primary=0, document_type="EX-21.1",
        description="Subsidiaries", cfg=c
    ) == "subsidiary_exhibit"
    assert classify_document(
        form="8-K", is_primary=0, document_type="EX-10.2",
        description="Credit Agreement", cfg=c
    ) == "material_contract_exhibit"
    assert classify_document(
        form="8-K", is_primary=0, document_type="EX-2.1",
        description="Merger Agreement", cfg=c
    ) == "transaction_exhibit"


def test_irrelevant_exhibit_is_not_selected():
    c = cfg()
    assert classify_document(
        form="8-K", is_primary=0, document_type="EX-99.1",
        description="Press release", cfg=c
    ) is None


def test_html_normalization_removes_script():
    raw = (
        b"<html><body><h1>Supplier Risk</h1>"
        b"<script>ignore_me()</script><p>We rely on Beta.</p></body></html>"
    )
    text = normalized_text(raw, "text/html")
    assert "Supplier Risk" in text
    assert "We rely on Beta." in text
    assert "ignore_me" not in text


def test_gzip_decode():
    body = b"hello relation corpus"
    gz = gzip.compress(body)
    assert decode_payload(gz, "gzip") == body


def test_chunking_is_deterministic_and_overlapping():
    text = ("alpha beta gamma. " * 1000).strip()
    a = chunks(text, 600, 50)
    b = chunks(text, 600, 50)
    assert a == b
    assert len(a) > 1
    assert all(0 <= s < e <= len(text) for s, e, _ in a)


def make_db(tmp_path: Path) -> tuple[Path, Path]:
    db = tmp_path / "market.db"
    raw_dir = tmp_path / "data/raw"
    raw_dir.mkdir(parents=True)
    payload = b"<html><body>We rely on Beta as a supplier.</body></html>"
    gz_path = raw_dir / "doc.gz"
    gz_path.write_bytes(gzip.compress(payload))
    sha = hashlib.sha256(payload).hexdigest()

    with sqlite3.connect(db) as conn:
        conn.executescript("""
        CREATE TABLE assets(
          asset_id INTEGER PRIMARY KEY,
          ticker TEXT,asset_type TEXT,active INTEGER
        );
        INSERT INTO assets VALUES (1,'AAA','equity',1);
        CREATE TABLE asset_entities(
          asset_id INTEGER PRIMARY KEY,entity_id INTEGER
        );
        INSERT INTO asset_entities VALUES (1,101);

        CREATE TABLE raw_source_documents(
          raw_document_id TEXT PRIMARY KEY,
          source_id TEXT,external_id TEXT,document_kind TEXT,
          source_url TEXT,canonical_url TEXT,published_at TEXT,
          available_at TEXT,retrieved_at TEXT,modified_at TEXT,
          content_type TEXT,content_encoding TEXT,raw_sha256 TEXT,
          storage_path TEXT,byte_length INTEGER,parser_status TEXT,
          parser_version TEXT,parent_raw_document_id TEXT,
          metadata_json TEXT,created_at TEXT
        );
        CREATE TABLE raw_document_assets(
          raw_document_id TEXT,asset_id INTEGER,role TEXT,
          linking_method TEXT,linking_version TEXT,confidence REAL,
          metadata_json TEXT
        );
        CREATE TABLE sec_filings(
          raw_document_id TEXT PRIMARY KEY,cik TEXT,accession_number TEXT,
          form TEXT,filing_date TEXT,acceptance_datetime TEXT,
          report_date TEXT,primary_document TEXT,
          primary_doc_description TEXT,is_amendment INTEGER,
          items_json TEXT,entity_name TEXT,ticker_at_ingestion TEXT,
          metadata_version TEXT
        );
        CREATE TABLE sec_filing_files(
          filing_raw_document_id TEXT,sequence_number TEXT,
          document_name TEXT,document_type TEXT,description TEXT,
          source_url TEXT,is_primary INTEGER,raw_document_id TEXT,
          metadata_json TEXT,inventory_raw_document_id TEXT,
          inventory_status TEXT,error_json TEXT,last_attempt_run_id TEXT
        );
        CREATE TABLE sec_filing_metadata_observations(
          metadata_observation_id TEXT PRIMARY KEY,
          filing_raw_document_id TEXT,metadata_version_id TEXT,
          normalized_raw_document_id TEXT,
          source_submission_retrieval_id TEXT,
          source_submissions_raw_document_id TEXT,
          ingestion_run_id TEXT,retrieval_identity TEXT,
          observation_sequence INTEGER,state_revision_number INTEGER,
          previous_observation_id TEXT,observation_kind TEXT,
          observed_at TEXT,retrieved_at TEXT,available_at TEXT,
          availability_basis TEXT,availability_is_point_in_time INTEGER,
          provenance_status TEXT,metadata_json TEXT,created_at TEXT
        );
        """)
        conn.execute(
            """
            INSERT INTO sec_filings VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "filing1","1","0001","10-K","2020-01-02",
                "2020-01-02T16:00:00+00:00","2019-12-31",
                "a.htm","10-K",0,"[]","Alpha","AAA","v1"
            ),
        )
        conn.execute(
            """
            INSERT INTO raw_source_documents VALUES
            (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
            """,
            (
                "content1","sec","0001:a.htm","sec_filing_file",
                "https://sec/a.htm","https://sec/a.htm",
                "2020-01-02T16:00:00+00:00",
                "2020-01-02T16:00:00+00:00",
                "2026-01-01T00:00:00+00:00",None,
                "text/html","gzip",sha,
                str(gz_path),len(payload),"raw",None,None,"{}",
                "2026-01-01"
            ),
        )
        conn.execute(
            """
            INSERT INTO raw_document_assets VALUES
            ('content1',1,'subject','sec_metadata','v1',1.0,'{}')
            """
        )
        conn.execute(
            """
            INSERT INTO sec_filing_files VALUES
            ('filing1','1','a.htm','10-K','Annual Report',
             'https://sec/a.htm',1,'content1','{}',NULL,'current',NULL,NULL)
            """
        )
        conn.execute(
            """
            INSERT INTO sec_filing_metadata_observations VALUES
            ('mo1','filing1','mv1',NULL,NULL,NULL,NULL,'r1',
             1,1,NULL,'initial',
             '2026-01-01T00:00:00+00:00',
             '2026-01-01T00:00:00+00:00',
             '2020-01-02T16:00:00+00:00',
             'sec_acceptance_historical',0,'historical','{}','2026-01-01')
            """
        )
        conn.commit()
    return db, gz_path


def test_plan_reads_real_storage_without_mutating_db(tmp_path, monkeypatch):
    db, _ = make_db(tmp_path)
    config = cfg()
    config["main_db"] = str(db)
    config_path = tmp_path / "cfg.json"
    config_path.write_text(json.dumps(config))

    before = db.read_bytes()
    out = plan(config_path)
    after = db.read_bytes()
    assert out["status"] == "PASS"
    assert out["selected_documents"] == 1
    assert out["selected_assets"] == 1
    assert out["missing_storage_count"] == 0
    assert before == after


def test_historical_corpus_never_claims_strict_pit():
    c = cfg()
    assert c["strict_historical_pit"] is False
    assert "historical reconstruction" in (
        c["clock_contract"]["point_in_time_policy"]
    )


def test_package_forbids_relation_writes():
    c = cfg()
    guards = c["hard_guards"]
    assert guards["no_relation_candidates_written"] is True
    assert guards["no_graph_edges_written"] is True
    assert guards["no_llm_calls"] is True

from __future__ import annotations

import json
import sqlite3
from pathlib import Path

from knowledge.entities.exact_entity_resolution_v001 import (
    ExactEntityResolver,
    normalize_entity_name,
)
from knowledge.relations.relation_candidate_extraction_v001 import (
    extract_agreement_counterparties,
    extract_exhibit21,
    reconstruct_document,
)


def test_name_normalization_is_conservative():
    assert normalize_entity_name("  Alpha,   Inc. ") == "alpha, inc"
    # We deliberately do NOT strip legal suffixes for auto-resolution.
    assert normalize_entity_name("Alpha Inc.") != normalize_entity_name("Alpha")


def test_ex21_extracts_suffix_anchored_subsidiaries():
    text = """EXHIBIT 21
Subsidiaries of Registrant
Name of Subsidiary
Alpha Holdings, Inc.
Delaware
Beta International GmbH
Germany
"""
    rows = extract_exhibit21(text)
    names = [x.target_name_raw for x in rows]
    assert "Alpha Holdings, Inc" in names
    assert "Beta International GmbH" in names
    assert all(x.relation_type == "parent_of" for x in rows)


def test_ex21_rejects_header_and_jurisdiction():
    text = """EXHIBIT 21
Place of Incorporation
Delaware
United Kingdom
"""
    assert extract_exhibit21(text) == []


def test_contract_preamble_extracts_legal_counterparties():
    text = (
        "CREDIT AGREEMENT\n"
        "This Credit Agreement is made by and between "
        "Alpha Holdings, Inc. and Beta Finance LLC, dated as of "
        "January 1, 2024.\nWHEREAS, the parties desire to..."
    )
    rows = extract_agreement_counterparties(text)
    names = {x.target_name_raw for x in rows}
    assert "Alpha Holdings, Inc" in names
    assert "Beta Finance LLC" in names
    assert all(x.relation_type == "contract_party_of" for x in rows)


def test_contract_extractor_requires_explicit_party_preamble():
    text = (
        "Alpha Holdings, Inc. sells products to Beta Finance LLC. "
        "This is general narrative text."
    )
    assert extract_agreement_counterparties(text) == []


def make_main_db(tmp_path: Path) -> Path:
    db = tmp_path / "main.db"
    with sqlite3.connect(db) as conn:
        conn.executescript("""
        CREATE TABLE entities(
          entity_id INTEGER PRIMARY KEY,
          canonical_name TEXT
        );
        CREATE TABLE assets(
          asset_id INTEGER PRIMARY KEY,
          ticker TEXT,
          name TEXT
        );
        CREATE TABLE asset_entities(
          asset_id INTEGER PRIMARY KEY,
          entity_id INTEGER
        );
        INSERT INTO entities VALUES (1,'Alpha Holdings, Inc.');
        INSERT INTO entities VALUES (2,'Beta Finance LLC');
        INSERT INTO assets VALUES (10,'ALPH','Alpha Holdings, Inc.');
        INSERT INTO assets VALUES (11,'BETA','Beta Finance LLC');
        INSERT INTO asset_entities VALUES (10,1);
        INSERT INTO asset_entities VALUES (11,2);
        """)
        conn.commit()
    return db


def test_exact_resolver_resolves_unique_asset_or_entity_alias(tmp_path):
    db = make_main_db(tmp_path)
    with sqlite3.connect(db) as conn:
        r = ExactEntityResolver(conn)
        assert r.resolve("Beta Finance LLC").entity_id == 2
        assert r.resolve("BETA").entity_id == 2


def test_exact_resolver_does_not_fuzzy_match(tmp_path):
    db = make_main_db(tmp_path)
    with sqlite3.connect(db) as conn:
        r = ExactEntityResolver(conn)
        assert r.resolve("Beta Finance").entity_id is None


def test_exact_resolver_marks_ambiguous_alias(tmp_path):
    db = make_main_db(tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO entities VALUES (3,'Other Corporation')"
        )
        conn.execute(
            "INSERT INTO assets VALUES (12,'BETA','Other Corp Alias')"
        )
        # ticker uniqueness is not assumed by the resolver schema itself.
        conn.execute(
            "INSERT INTO asset_entities VALUES (12,3)"
        )
        conn.commit()
        r = ExactEntityResolver(conn)
        assert r.resolve("BETA").status == "ambiguous_exact"


def test_reconstruct_document_preserves_offsets(tmp_path):
    db = tmp_path / "corpus.db"
    text = "abcdefghij"
    with sqlite3.connect(db) as conn:
        conn.execute("""
        CREATE TABLE corpus_chunks(
          corpus_document_id TEXT,
          chunk_index INTEGER,
          char_start INTEGER,
          char_end INTEGER,
          chunk_text TEXT
        )
        """)
        conn.executemany(
            "INSERT INTO corpus_chunks VALUES (?,?,?,?,?)",
            [
                ("d",0,0,7,text[0:7]),
                ("d",1,5,10,text[5:10]),
            ],
        )
        conn.commit()
        rebuilt = reconstruct_document(conn, "d", len(text))
    assert rebuilt == text


def test_config_defers_semantic_relationships():
    cfg = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "config/event_graph_relation_candidates_v001.json"
        ).read_text()
    )
    assert "supplier_of" in cfg["deferred_extractors"]
    assert "customer_of" in cfg["deferred_extractors"]
    assert "competitor_of" in cfg["deferred_extractors"]
    assert cfg["promotion_gate"]["no_promotion_in_this_package"] is True


def test_candidate_contract_assigns_no_market_semantics():
    cfg = json.loads(
        (
            Path(__file__).resolve().parents[1]
            / "config/event_graph_relation_candidates_v001.json"
        ).read_text()
    )
    contract = cfg["candidate_contract"]
    assert contract["market_direction_assigned"] is False
    assert contract["market_weight_assigned"] is False
    assert contract["candidate_sign_assigned"] is False
    assert contract["candidates_are_not_graph_edges"] is True

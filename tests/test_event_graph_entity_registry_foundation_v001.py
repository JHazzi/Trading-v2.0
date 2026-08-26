import json,sqlite3
from pathlib import Path

from knowledge.entities.entity_registry_foundation_v001 import (
    normalize_name,quality_reason
)

ROOT=Path(__file__).resolve().parents[1]

def cfg():
    return json.loads(
      (ROOT/"config/event_graph_entity_registry_foundation_v001.json").read_text()
    )

def test_rejects_organized_or_incorporated_header():
    assert quality_reason("Organized or Incorporated",cfg())=="known_header"

def test_keeps_real_company_name():
    assert quality_reason("Apple Asia LLC",cfg()) is None

def test_exact_normalization_does_not_strip_legal_suffix():
    assert normalize_name("Apple Asia LLC")=="apple asia llc"
    assert normalize_name("Apple Asia")!="apple asia llc"

def test_contract_branch_is_excluded():
    c=cfg()
    assert c["contract_claims_in_scope"] is False
    assert c["contract_branch_status"][
      "event_graph_relation_evidence_v002_contract_party_mention"
    ]=="qa_failed_not_registry_input"

def test_no_entity_creation_or_alias_merge():
    c=cfg()["identity_contract"]
    assert c["main_entities_created"] is False
    assert c["main_entities_updated"] is False
    assert c["fuzzy_merge"] is False
    assert c["cross_name_alias_merge"] is False

def test_no_graph_promotion():
    c=cfg()["promotion_gate"]
    assert c["no_graph_edges"] is True
    assert c["no_relation_promotion"] is True

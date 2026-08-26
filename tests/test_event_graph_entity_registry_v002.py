import json
from pathlib import Path
from knowledge.entities.entity_registry_v002 import (
    normalize_text,bucket_key,Observation
)

ROOT=Path(__file__).resolve().parents[1]

def cfg():
    return json.loads(
      (ROOT/"config/event_graph_entity_registry_v002.json").read_text()
    )

def obs(name,juri,reg=1,dba=None):
    return Observation(
      "r","d","a",10,reg,"T","2020-01-01T00:00:00+00:00",
      name,name,juri,None,dba,None,None,"[]",0,1,"explicit_header",
      "sha",None
    )

def test_same_name_same_jurisdiction_same_registrant_groups():
    assert bucket_key(obs("Acme, Inc.","Delaware")) == bucket_key(
      obs("Acme, Inc.","Delaware")
    )

def test_same_name_different_jurisdiction_does_not_group():
    assert bucket_key(obs("Acme Ltd","India")) != bucket_key(
      obs("Acme Ltd","Zimbabwe")
    )

def test_same_name_same_jurisdiction_different_registrant_does_not_group():
    assert bucket_key(obs("Acme Ltd","India",1)) != bucket_key(
      obs("Acme Ltd","India",2)
    )

def test_missing_jurisdiction_is_scoped_to_registrant():
    assert bucket_key(obs("Acme Ltd",None,1)) != bucket_key(
      obs("Acme Ltd",None,2)
    )

def test_normalization_does_not_strip_legal_suffix():
    assert normalize_text("Acme, Inc.")=="acme, inc"
    assert normalize_text("Acme Incorporated")=="acme incorporated"
    assert normalize_text("Acme, Inc.")!=normalize_text("Acme Incorporated")

def test_dba_is_not_in_bucket_key():
    assert bucket_key(obs("Acme Ltd","Canada",1,"Acme Canada")) == bucket_key(
      obs("Acme Ltd","Canada",1,"Acme North")
    )

def test_hard_guards_all_enabled():
    c=cfg()["hard_guards"]
    assert all(c.values())

def test_no_fuzzy_or_jurisdiction_mapping():
    n=cfg()["normalization"]
    assert n["fuzzy_matching"] is False
    assert n["jurisdiction_mapping"] is False
    assert n["strip_legal_suffix"] is False

def test_registry_is_not_canonical_identity():
    s=cfg()["registry_semantics"]
    assert "not yet a canonical entity" in s["record_meaning"]
    assert s["dba_is_alias_evidence_not_identity_key"] is True
    assert s["ownership_is_relation_evidence_not_identity_key"] is True

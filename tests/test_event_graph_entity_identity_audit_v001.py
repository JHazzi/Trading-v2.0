import json
from pathlib import Path
from knowledge.entities.entity_identity_audit_v001 import (
    canonical_identity_key,legal_form_signature,normalize_basic
)

ROOT=Path(__file__).resolve().parents[1]

def cfg():
    return json.loads(
      (ROOT/"config/event_graph_entity_identity_audit_v001.json").read_text()
    )

def test_inc_and_incorporated_have_same_key():
    c=cfg()
    assert canonical_identity_key("Acme, Inc.",c)==canonical_identity_key(
      "Acme Incorporated",c
    )

def test_llc_and_inc_do_not_have_same_key():
    c=cfg()
    assert canonical_identity_key("Acme LLC",c)!=canonical_identity_key(
      "Acme Inc.",c
    )

def test_suffix_is_preserved_as_family():
    c=cfg()
    stem,family=legal_form_signature("Acme Holdings, Inc.",c)
    assert stem=="acme holdings"
    assert family=="INC"

def test_sa_de_cv_prefers_long_form_family():
    c=cfg()
    stem,family=legal_form_signature("Acme Mexico, S.A. de C.V.",c)
    assert stem=="acme mexico"
    assert family=="SA_DE_CV"

def test_punctuation_normalization_is_conservative():
    assert normalize_basic("Acme, Inc.")=="acme inc"

def test_auto_merge_is_disabled():
    c=cfg()
    assert c["candidate_rules"]["automatic_merge_allowed"] is False
    assert c["promotion_gate"]["no_merge_in_this_package"] is True

def test_fuzzy_and_llm_identity_are_disabled():
    c=cfg()["candidate_rules"]
    assert c["fuzzy_similarity"] is False
    assert c["semantic_embedding_similarity"] is False
    assert c["llm_identity_decision"] is False

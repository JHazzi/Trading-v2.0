import json
from pathlib import Path
from knowledge.entities.identity_conflict_qa_v001 import load_config

ROOT=Path(__file__).resolve().parents[1]

def cfg():
    return json.loads(
      (ROOT/"config/event_graph_identity_conflict_qa_v001.json").read_text()
    )

def test_all_hard_guards_enabled():
    assert all(cfg()["hard_guards"].values())

def test_no_automatic_identity_verdicts():
    c=cfg()["classification_contract"]
    assert c["automatic_merge"] is False
    assert c["automatic_split"] is False
    assert c["jurisdiction_alias_mapping"] is False
    assert c["fuzzy_identity_matching"] is False
    assert c["llm_identity_decision"] is False

def test_same_accession_is_evidence_not_verdict():
    c=cfg()["classification_contract"]
    assert c["same_accession_cooccurrence_is_evidence_not_verdict"] is True
    assert c["temporal_nonoverlap_is_evidence_not_verdict"] is True

def test_config_loads():
    c=load_config(
      ROOT/"config/event_graph_identity_conflict_qa_v001.json"
    )
    assert c["version"]=="event_graph_identity_conflict_qa_v001"

import json
from pathlib import Path
from knowledge.entities.identity_resolution_foundation_v001 import (
    reference_relation,classify_pair,row_quality_reason
)

ROOT=Path(__file__).resolve().parents[1]

def cfg():
    return json.loads(
      (ROOT/"config/event_graph_identity_resolution_foundation_v001.json").read_text()
    )

def ref():
    return json.loads(
      (ROOT/"config/jurisdiction_reference_candidates_v001.json").read_text()
    )

def pair(left,right,shared=0,temp="evidence_ranges_overlap",dba=0):
    return {
      "left_jurisdiction":left,
      "right_jurisdiction":right,
      "shared_accessions":[f"a{i}" for i in range(shared)],
      "temporal_relation":temp,
      "dba_cross_match_count":dba,
    }

def test_venezuela_name_variants_are_reference_equivalent_candidate():
    r=reference_relation("Venezuela","Venezuela, Bolivarian Republic of",ref())
    assert r["kind"]=="reference_equivalent_candidate"

def test_isreal_typo_is_reference_equivalent_candidate():
    r=reference_relation("Israel","Isreal",ref())
    assert r["kind"]=="reference_equivalent_candidate"
    assert r["candidate_kind"]=="spelling_typo_candidate"

def test_us_country_vs_state_is_hierarchy_not_equivalence():
    r=reference_relation("United States","Washington",ref())
    assert r["kind"]=="hierarchical_granularity_candidate"

def test_same_accession_distinct_jurisdictions_are_not_auto_split():
    out=classify_pair(pair("Thailand","United Kingdom",shared=5),ref())
    assert out["review_class"]=="same_accession_distinct_or_source_error"
    assert out["automatic_split"] is False
    assert out["identity_verdict"] is None

def test_nonoverlap_distinct_jurisdictions_are_rejurisdiction_candidate():
    out=classify_pair(pair(
      "Texas","Delaware",0,"left_before_right_no_overlap"
    ),ref())
    assert out["review_class"]=="temporal_rejurisdiction_or_reporting_change_candidate"
    assert out["automatic_merge"] is False

def test_overlap_distinct_jurisdictions_remains_unresolved():
    out=classify_pair(pair("Germany","Switzerland",0,"evidence_ranges_overlap"),ref())
    assert out["review_class"]=="unresolved_overlap_distinct_jurisdiction"

def test_section_headings_are_quality_candidates():
    reasons=row_quality_reason("International Subsidiaries:",None,cfg())
    assert "section_heading_candidate" in reasons
    assert "missing_jurisdiction" in reasons

def test_test_entity_is_quality_candidate():
    reasons=row_quality_reason("Consumer Test Entity (TEST PURPOSE ONLY)",None,cfg())
    assert "test_placeholder_candidate" in reasons

def test_missing_jurisdiction_alone_is_not_automatic_exclusion():
    reasons=row_quality_reason("Legitimate Entity LLC",None,cfg())
    assert reasons==["missing_jurisdiction"]
    assert cfg()["row_quality_rules"]["automatic_exclusion"] is False

def test_all_identity_decisions_remain_disabled():
    c=cfg()["classification_contract"]
    assert all(v is False for v in c.values())

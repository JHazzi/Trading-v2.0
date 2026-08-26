from pathlib import Path
import json
from knowledge.relations.relation_evidence_extraction_v002 import (
    extract_ex21,extract_contract_parties,quality_flags
)

def names(rows):
    return [x.named_entity_raw for x in rows]

def test_ex21_longest_suffix_company_llc():
    text="Marine Well Containment Company LLC (5)\n12.5\nDelaware\n"
    assert names(extract_ex21(text))==["Marine Well Containment Company LLC"]

def test_ex21_longest_suffix_sa_de_cv():
    text="Cordis de Mexico, S.A. de C.V.\nMexico\n"
    assert names(extract_ex21(text))==["Cordis de Mexico, S.A. de C.V"]

def test_ex21_rejects_header_where_incorporated():
    assert extract_ex21("Name\nWhere Incorporated\nMicrosoft Ireland Research\n")==[]

def test_ex21_rejects_broken_parenthetical_prefix():
    assert extract_ex21("Shanghai) Ltd\nChina\n")==[]

def test_contract_extracts_two_real_orgs_without_jurisdiction():
    text=(
      "AGREEMENT\nThis Agreement is by and between "
      "Chevron U.S.A. Inc., a Pennsylvania Corporation, with offices at "
      "1400 Smith Street, Houston, Texas 77002, and HFO Holdings LLC, "
      "a Delaware limited liability company.\nRECITALS\n"
    )
    n=names(extract_contract_parties(text))
    assert "Chevron U.S.A. Inc" in n
    assert "HFO Holdings LLC" in n
    assert "Pennsylvania Corporation" not in n

def test_contract_does_not_emit_table_of_contents():
    text=(
      "AGREEMENT\nTABLE OF CONTENTS\nArticle 1 Definitions\n"
      "Section 2.02 Conversion of Shares\n"
      "This is by and among the Company and SPQR, LLC\n"
    )
    assert extract_contract_parties(text)==[]

def test_contract_does_not_fuse_grantor_with_bank():
    text=(
      "This Agreement is by and between Microsoft, as Grantor, and "
      "The Bank of New York Mellon Trust Company, N.A., as Trustee.\n"
      "RECITALS\n"
    )
    n=names(extract_contract_parties(text))
    assert "The Bank of New York Mellon Trust Company, N.A" in n
    assert all("Grantor, and" not in x for x in n)

def test_contract_rejects_role_only_phrase():
    text=(
      "This agreement is by and among the Acquiror, the Company "
      "and certain other parties thereto.\nRECITALS\n"
    )
    assert extract_contract_parties(text)==[]

def test_contract_party_claim_is_not_pairwise_edge():
    cfg=json.loads((
      Path(__file__).resolve().parents[1]/
      "config/event_graph_relation_evidence_v002.json"
    ).read_text())
    assert cfg["hard_guards"]["no_pairwise_contract_edges"] is True
    assert cfg["hard_guards"]["no_direct_parent_claim_from_ex21"] is True

def test_deferred_supplier_customer_remains_deferred():
    cfg=json.loads((
      Path(__file__).resolve().parents[1]/
      "config/event_graph_relation_evidence_v002.json"
    ).read_text())
    assert "supplier_of" in cfg["deferred"]
    assert "customer_of" in cfg["deferred"]

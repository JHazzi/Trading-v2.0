from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_CONFIG=ROOT/"config/event_graph_relation_evidence_v002.json"

def audit(config_path:Path=DEFAULT_CONFIG)->dict:
    cfg=json.loads(config_path.read_text())
    db=ROOT/cfg["output_db"]
    failures=[]
    reviews=[]
    with sqlite3.connect(db) as c:
        total=c.execute("SELECT COUNT(*) FROM evidence_claims").fetchone()[0]
        kinds=dict(c.execute(
          "SELECT claim_kind,COUNT(*) FROM evidence_claims GROUP BY claim_kind"
        ).fetchall())
        resolution=dict(c.execute(
          "SELECT resolution_status,COUNT(*) FROM evidence_claims GROUP BY resolution_status"
        ).fetchall())
        bad_pit=c.execute(
          "SELECT COUNT(*) FROM evidence_claims WHERE availability_is_point_in_time<>0"
        ).fetchone()[0]
        edge_ready=c.execute(
          "SELECT COUNT(*) FROM evidence_claims WHERE edge_ready<>0"
        ).fetchone()[0]
        bad_span=c.execute(
          """SELECT COUNT(*) FROM evidence_claims
             WHERE evidence_char_end<=evidence_char_start
                OR TRIM(evidence_text)=''"""
        ).fetchone()[0]
        graph_tables=[
          r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
          )
          if any(x in r[0].lower() for x in ("assertion","propagation","edge"))
        ]
        party_sets=c.execute(
          "SELECT COUNT(*) FROM contract_party_sets"
        ).fetchone()[0]
        bad_party_sets=c.execute(
          "SELECT COUNT(*) FROM contract_party_sets WHERE party_count<1"
        ).fetchone()[0]
        self_named=c.execute(
          """SELECT COUNT(*) FROM evidence_claims
             WHERE resolved_named_entity_id=registrant_entity_id"""
        ).fetchone()[0]

    if total==0: failures.append("zero_evidence_claims")
    if bad_pit: failures.append("pit_claim_present")
    if edge_ready: failures.append("edge_ready_claim_present")
    if bad_span: failures.append("invalid_evidence_span")
    if graph_tables: failures.append("graph_tables_present")
    if bad_party_sets: failures.append("empty_contract_party_set")
    if "reported_subsidiary_of_registrant" not in kinds:
        failures.append("zero_ex21_claims")
    if "contract_party_mention" not in kinds:
        reviews.append("zero_contract_party_mentions")
    if resolution.get("unresolved",0):
        reviews.append("unresolved_named_entities_expected")
    if self_named:
        reviews.append(
          "registrant_name_appears_as_contract_party_or_ex21_name_not_edge"
        )

    return {
      "status":"FAIL" if failures else ("REVIEW" if reviews else "PASS"),
      "failures":failures,
      "reviews":reviews,
      "claims":int(total),
      "by_claim_kind":kinds,
      "by_resolution_status":resolution,
      "contract_party_sets":int(party_sets),
      "self_named_entity_claims":int(self_named),
      "semantic_contract":{
        "ex21_direct_parent_edge":False,
        "ex21_claim_is_reported_subsidiary":True,
        "registrant_assumed_contract_party":False,
        "contract_pairwise_edges_created":False,
      },
      "causal_contract":{
        "strict_historical_pit":False,
        "all_claims_pit_zero":bad_pit==0,
      },
      "graph_contract":{
        "edge_ready_claims":int(edge_ready),
        "graph_tables_present":graph_tables,
        "promotion_allowed":False,
      },
      "next_gate":(
        "Review V002 QA. Only after evidence precision is acceptable should "
        "entity-registry creation/resolution be designed. Do not start "
        "supplier/customer semantic extraction yet."
      )
    }

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    a=p.parse_args()
    print(json.dumps(audit(a.config),indent=2))

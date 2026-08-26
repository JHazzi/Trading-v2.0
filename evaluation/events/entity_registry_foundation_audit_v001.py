from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_CONFIG=ROOT/"config/event_graph_entity_registry_foundation_v001.json"

def audit(config_path:Path=DEFAULT_CONFIG)->dict:
    cfg=json.loads(config_path.read_text())
    db=ROOT/cfg["output_db"]
    failures=[]
    reviews=[]
    with sqlite3.connect(db) as c:
        run=c.execute(
          "SELECT status,source_claims,accepted_claims,name_records "
          "FROM registry_runs ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        names=c.execute(
          "SELECT COUNT(*) FROM registry_name_records"
        ).fetchone()[0]
        evidence=c.execute(
          "SELECT COUNT(*) FROM registry_name_evidence"
        ).fetchone()[0]
        rejected=c.execute(
          "SELECT COUNT(*) FROM registry_rejections"
        ).fetchone()[0]
        bad_pit=c.execute(
          "SELECT COUNT(*) FROM registry_name_evidence "
          "WHERE availability_is_point_in_time<>0"
        ).fetchone()[0]
        conflicts=c.execute(
          "SELECT COUNT(*) FROM registry_name_records "
          "WHERE identity_status='conflicting_existing_exact_entities'"
        ).fetchone()[0]
        existing=c.execute(
          "SELECT COUNT(*) FROM registry_name_records "
          "WHERE identity_status='existing_exact_entity_observed'"
        ).fetchone()[0]
        unresolved=c.execute(
          "SELECT COUNT(*) FROM registry_name_records "
          "WHERE identity_status='unresolved_name_registry_record'"
        ).fetchone()[0]
        graphish=[
          r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
          )
          if any(x in r[0].lower() for x in ("edge","assertion","propagation"))
        ]
    if run is None or run[0]!="completed":
        failures.append("registry_run_not_completed")
    if names==0 or evidence==0:
        failures.append("empty_registry")
    if bad_pit:
        failures.append("registry_incorrectly_claims_pit")
    if conflicts:
        reviews.append("conflicting_existing_exact_entity_ids")
    if graphish:
        failures.append("registry_db_contains_graph_tables")
    if unresolved:
        reviews.append("unresolved_registry_names_expected")
    return {
      "status":"FAIL" if failures else ("REVIEW" if reviews else "PASS"),
      "failures":failures,
      "reviews":reviews,
      "name_records":int(names),
      "evidence_rows":int(evidence),
      "rejected_claims":int(rejected),
      "identity_status_counts":{
        "existing_exact_entity_observed":int(existing),
        "unresolved_name_registry_record":int(unresolved),
        "conflicting_existing_exact_entities":int(conflicts),
      },
      "causal_contract":{
        "strict_historical_pit":False,
        "all_evidence_pit_zero":bad_pit==0,
      },
      "scope_contract":{
        "ex21_only":True,
        "contract_party_claims_ingested":False,
        "canonical_entities_created":False,
        "cross_name_alias_merge_performed":False,
      },
      "graph_contract":{
        "graph_tables_present":graphish,
        "graph_edges_written":False,
        "relation_promotion":False,
      },
      "next_gate":(
        "Inspect registry size, repeated-name evidence and any existing exact "
        "entity conflicts. Then design identity resolution/alias evidence. "
        "Do not promote graph edges yet."
      )
    }

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    a=p.parse_args()
    print(json.dumps(audit(a.config),indent=2))

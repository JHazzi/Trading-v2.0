from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_CONFIG=ROOT/"config/event_graph_entity_registry_v002.json"

def audit(config_path:Path=DEFAULT_CONFIG)->dict:
    cfg=json.loads(config_path.read_text())
    db=ROOT/cfg["output_db"]
    failures=[]
    reviews=[]
    with sqlite3.connect(db) as c:
        run=c.execute(
          "SELECT status,source_rows,buckets_written,evidence_rows_written,"
          "alias_evidence_rows_written FROM registry_runs "
          "ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        buckets=c.execute(
          "SELECT COUNT(*) FROM identity_evidence_buckets"
        ).fetchone()[0]
        evidence=c.execute(
          "SELECT COUNT(*) FROM identity_bucket_evidence"
        ).fetchone()[0]
        aliases=c.execute(
          "SELECT COUNT(*) FROM identity_alias_evidence"
        ).fetchone()[0]
        missing_juri=c.execute(
          "SELECT COUNT(*) FROM identity_evidence_buckets "
          "WHERE jurisdiction_status='missing'"
        ).fetchone()[0]
        auto_alias=c.execute(
          "SELECT COUNT(*) FROM identity_alias_evidence "
          "WHERE auto_merge_allowed<>0"
        ).fetchone()[0]
        bad_status=c.execute(
          "SELECT COUNT(*) FROM identity_evidence_buckets "
          "WHERE identity_status<>'evidence_bucket_not_canonical'"
        ).fetchone()[0]
        cross_juri_name=c.execute(
          """
          SELECT COUNT(*) FROM (
            SELECT registrant_entity_id,normalized_legal_name
            FROM identity_evidence_buckets
            GROUP BY registrant_entity_id,normalized_legal_name
            HAVING COUNT(DISTINCT normalized_jurisdiction)>1
          )
          """
        ).fetchone()[0]
        graphish=[
          r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
          )
          if any(x in r[0].lower() for x in ("edge","assertion","propagation"))
        ]
    if run is None or run[0]!="completed":
        failures.append("registry_run_not_completed")
    if buckets==0 or evidence==0:
        failures.append("empty_registry_v002")
    if run and int(run[1])!=int(evidence):
        failures.append("source_evidence_row_count_mismatch")
    if auto_alias:
        failures.append("alias_auto_merge_enabled")
    if bad_status:
        failures.append("canonical_identity_status_present")
    if graphish:
        failures.append("registry_v002_contains_graph_tables")
    if missing_juri:
        reviews.append("missing_jurisdiction_buckets_require_later_resolution")
    if cross_juri_name:
        reviews.append("same_name_multiple_jurisdictions_preserved_as_distinct")
    return {
      "status":"FAIL" if failures else ("REVIEW" if reviews else "PASS"),
      "failures":failures,
      "reviews":reviews,
      "buckets":int(buckets),
      "evidence_rows":int(evidence),
      "alias_evidence_rows":int(aliases),
      "missing_jurisdiction_buckets":int(missing_juri),
      "same_registrant_name_multiple_jurisdiction_groups":int(cross_juri_name),
      "identity_contract":{
        "canonical_entities_created":False,
        "alias_auto_merge_rows":int(auto_alias),
        "cross_registrant_merge":False,
        "cross_jurisdiction_merge":False,
      },
      "graph_contract":{
        "graph_tables_present":graphish,
        "graph_edges_written":False,
        "relation_promotion":False,
      },
      "next_gate":(
        "QA conflict buckets and alias evidence. Then design cross-bucket "
        "canonical identity resolution with jurisdiction/history/DBA evidence "
        "and external identifiers. Do not create graph edges yet."
      )
    }

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    a=p.parse_args()
    print(json.dumps(audit(a.config),indent=2))

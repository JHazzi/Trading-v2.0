from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_CONFIG=ROOT/"config/event_graph_identity_conflict_qa_v001.json"

def audit(config_path:Path=DEFAULT_CONFIG)->dict:
    cfg=json.loads(config_path.read_text())
    db=ROOT/cfg["output_db"]
    failures=[]
    reviews=[]
    with sqlite3.connect(db) as c:
        run=c.execute(
          "SELECT status,conflict_groups_written,conflict_buckets_written,"
          "evidence_rows_written,missing_buckets_written "
          "FROM conflict_qa_runs ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        groups=c.execute(
          "SELECT COUNT(*) FROM identity_conflict_groups"
        ).fetchone()[0]
        buckets=c.execute(
          "SELECT COUNT(*) FROM identity_conflict_buckets"
        ).fetchone()[0]
        evidence=c.execute(
          "SELECT COUNT(*) FROM identity_conflict_evidence"
        ).fetchone()[0]
        pairs=c.execute(
          "SELECT COUNT(*) FROM conflict_bucket_pairs"
        ).fetchone()[0]
        shared_pairs=c.execute(
          "SELECT COUNT(*) FROM conflict_bucket_pairs "
          "WHERE shared_accession_count>0"
        ).fetchone()[0]
        overlap_pairs=c.execute(
          "SELECT COUNT(*) FROM conflict_bucket_pairs "
          "WHERE evidence_ranges_overlap<>0"
        ).fetchone()[0]
        nonoverlap_pairs=c.execute(
          "SELECT COUNT(*) FROM conflict_bucket_pairs "
          "WHERE left_before_right<>0 OR right_before_left<>0"
        ).fetchone()[0]
        missing=c.execute(
          "SELECT COUNT(*) FROM missing_jurisdiction_buckets"
        ).fetchone()[0]
        decisions=c.execute(
          "SELECT COUNT(*) FROM identity_conflict_groups "
          "WHERE decision_status<>'unresolved' OR manual_label IS NOT NULL"
        ).fetchone()[0]
        graphish=[
          r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
          )
          if any(x in r[0].lower() for x in ("edge","assertion","propagation"))
        ]
    if run is None or run[0]!="completed":
        failures.append("conflict_qa_run_not_completed")
    if groups==0 or buckets==0:
        failures.append("empty_conflict_qa")
    if decisions:
        failures.append("automatic_or_persisted_identity_decision_present")
    if graphish:
        failures.append("conflict_qa_contains_graph_tables")
    if shared_pairs:
        reviews.append("same_accession_conflict_pairs_require_manual_review")
    if missing:
        reviews.append("missing_jurisdiction_buckets_require_manual_review")
    return {
      "status":"FAIL" if failures else ("REVIEW" if reviews else "PASS"),
      "failures":failures,
      "reviews":reviews,
      "conflict_groups":int(groups),
      "conflict_buckets":int(buckets),
      "conflict_evidence_rows":int(evidence),
      "conflict_pairs":int(pairs),
      "shared_accession_pairs":int(shared_pairs),
      "temporal_overlap_pairs":int(overlap_pairs),
      "temporal_nonoverlap_pairs":int(nonoverlap_pairs),
      "missing_jurisdiction_buckets":int(missing),
      "identity_contract":{
        "automatic_decisions_written":int(decisions),
        "canonical_entities_created":False,
        "identity_merges_performed":False,
        "jurisdiction_normalization_writeback":False,
      },
      "graph_contract":{
        "graph_tables_present":graphish,
        "graph_edges_written":False,
        "relation_promotion":False,
      },
      "next_gate":(
        "Review complete conflict_evidence.json. Then define a versioned "
        "jurisdiction reference layer and explicit manual/scientific labels "
        "for conflict classes before cross-bucket canonical identity resolution."
      )
    }

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    a=p.parse_args()
    print(json.dumps(audit(a.config),indent=2))

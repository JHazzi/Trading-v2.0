from __future__ import annotations
import argparse,json,sqlite3
from pathlib import Path

ROOT=Path(__file__).resolve().parents[2]
DEFAULT_CONFIG=ROOT/"config/event_graph_identity_resolution_foundation_v001.json"

def audit(config_path:Path=DEFAULT_CONFIG)->dict:
    cfg=json.loads(config_path.read_text())
    db=ROOT/cfg["output_db"]
    failures=[]
    reviews=[]
    with sqlite3.connect(db) as c:
        run=c.execute(
          "SELECT status,conflict_groups,conflict_pairs,row_quality_candidates "
          "FROM foundation_runs ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        groups=c.execute(
          "SELECT COUNT(*) FROM identity_conflict_group_reviews"
        ).fetchone()[0]
        pairs=c.execute(
          "SELECT COUNT(*) FROM identity_conflict_pair_reviews"
        ).fetchone()[0]
        quality=c.execute(
          "SELECT COUNT(*) FROM row_quality_candidates"
        ).fetchone()[0]
        identity_verdicts=c.execute(
          "SELECT COUNT(*) FROM identity_conflict_pair_reviews "
          "WHERE identity_verdict IS NOT NULL"
        ).fetchone()[0]
        auto_merge=c.execute(
          "SELECT COUNT(*) FROM identity_conflict_pair_reviews "
          "WHERE automatic_merge<>0"
        ).fetchone()[0]
        auto_split=c.execute(
          "SELECT COUNT(*) FROM identity_conflict_pair_reviews "
          "WHERE automatic_split<>0"
        ).fetchone()[0]
        exclusions=c.execute(
          "SELECT COUNT(*) FROM row_quality_candidates "
          "WHERE exclusion_applied<>0 OR automatic_exclusion<>0"
        ).fetchone()[0]
        ref_write=c.execute(
          "SELECT "
          "(SELECT COUNT(*) FROM jurisdiction_reference_candidates "
          " WHERE writeback_allowed<>0) + "
          "(SELECT COUNT(*) FROM jurisdiction_hierarchy_candidates "
          " WHERE writeback_allowed<>0)"
        ).fetchone()[0]
        by_class=dict(c.execute(
          "SELECT review_class,COUNT(*) FROM identity_conflict_pair_reviews "
          "GROUP BY review_class ORDER BY review_class"
        ).fetchall())
        graphish=[
          r[0] for r in c.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
          )
          if any(x in r[0].lower() for x in ("edge","assertion","propagation"))
        ]

    if run is None or run[0]!="completed":
        failures.append("foundation_run_not_completed")
    if groups!=28:
        failures.append("unexpected_conflict_group_count")
    if pairs!=30:
        failures.append("unexpected_conflict_pair_count")
    if quality!=3:
        failures.append("unexpected_row_quality_candidate_count")
    if identity_verdicts:
        failures.append("identity_verdict_written_prematurely")
    if auto_merge or auto_split:
        failures.append("automatic_identity_decision_enabled")
    if exclusions:
        failures.append("row_exclusion_applied_prematurely")
    if ref_write:
        failures.append("jurisdiction_reference_writeback_enabled")
    if graphish:
        failures.append("foundation_contains_graph_tables")

    if quality:
        reviews.append("row_quality_candidates_require_confirmation")
    if by_class.get("same_accession_distinct_or_source_error",0):
        reviews.append("same_accession_distinct_or_source_error_requires_review")
    if by_class.get("temporal_rejurisdiction_or_reporting_change_candidate",0):
        reviews.append("temporal_rejurisdiction_candidates_require_review")

    return {
      "status":"FAIL" if failures else ("REVIEW" if reviews else "PASS"),
      "failures":failures,
      "reviews":reviews,
      "conflict_groups":int(groups),
      "conflict_pairs":int(pairs),
      "pair_review_classes":by_class,
      "row_quality_candidates":int(quality),
      "identity_contract":{
        "identity_verdicts_written":int(identity_verdicts),
        "automatic_merge_rows":int(auto_merge),
        "automatic_split_rows":int(auto_split),
        "canonical_entities_created":False,
      },
      "row_quality_contract":{
        "exclusions_applied":int(exclusions),
      },
      "jurisdiction_contract":{
        "reference_writeback_rows":int(ref_write),
        "authoritative_global_reference":False,
      },
      "graph_contract":{
        "graph_tables_present":graphish,
        "graph_edges_written":False,
        "relation_promotion":False,
      },
      "next_gate":(
        "Review the complete QA report. Confirm the three non-entity row-quality "
        "candidates and jurisdiction reference candidates. Then create an upstream "
        "Structured Rows V002 hygiene patch and rebuild Registry V2 before any "
        "canonical entity creation."
      )
    }

if __name__=="__main__":
    p=argparse.ArgumentParser()
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    a=p.parse_args()
    print(json.dumps(audit(a.config),indent=2))

from __future__ import annotations
import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/event_graph_entity_identity_audit_v001.json"


def audit(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(config_path.read_text())
    db = ROOT / cfg["output_db"]
    failures = []
    reviews = []

    with sqlite3.connect(db) as c:
        run = c.execute(
            "SELECT status,profiles_written,candidate_pairs_written "
            "FROM identity_runs ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        profiles = c.execute(
            "SELECT COUNT(*) FROM identity_name_profiles"
        ).fetchone()[0]
        pairs = c.execute(
            "SELECT COUNT(*) FROM identity_candidate_pairs"
        ).fetchone()[0]
        auto_merge_rows = c.execute(
            "SELECT COUNT(*) FROM identity_candidate_pairs "
            "WHERE auto_merge_allowed<>0"
        ).fetchone()[0]
        conflicts = c.execute(
            "SELECT COUNT(*) FROM identity_candidate_pairs "
            "WHERE same_accession_cooccurrence<>0"
        ).fetchone()[0]
        kinds = dict(c.execute(
            "SELECT candidate_kind,COUNT(*) FROM identity_candidate_pairs "
            "GROUP BY candidate_kind ORDER BY candidate_kind"
        ).fetchall())
        graphish = [
            r[0] for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            if any(
                x in r[0].lower()
                for x in ("edge", "assertion", "propagation")
            )
        ]

    if run is None or run[0] != "completed":
        failures.append("identity_run_not_completed")
    if profiles == 0:
        failures.append("zero_identity_profiles")
    if auto_merge_rows:
        failures.append("automatic_merge_candidate_row_present")
    if graphish:
        failures.append("identity_db_contains_graph_tables")
    if pairs == 0:
        reviews.append("zero_cross_name_identity_candidates")
    if conflicts:
        reviews.append("same_accession_candidate_conflicts_require_review")

    return {
        "status": "FAIL" if failures else ("REVIEW" if reviews else "PASS"),
        "failures": failures,
        "reviews": reviews,
        "profiles": int(profiles),
        "candidate_pairs": int(pairs),
        "same_accession_conflict_pairs": int(conflicts),
        "by_candidate_kind": kinds,
        "identity_contract": {
            "automatic_merge_allowed_by_contract": False,
            "automatic_merge_candidate_rows": int(auto_merge_rows),
            "automatic_merge_performed": False,
            "canonical_entities_created": False,
            "fuzzy_matching_used": False,
        },
        "graph_contract": {
            "graph_tables_present": graphish,
            "graph_edges_written": False,
            "relation_promotion": False,
        },
        "next_gate": (
            "Review exact shared-accession evidence for conflict pairs. "
            "Then classify identity candidates before any canonical entity creation."
        ),
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    a = p.parse_args()
    print(json.dumps(audit(a.config), indent=2))

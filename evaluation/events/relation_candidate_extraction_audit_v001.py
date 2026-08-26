from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = (
    ROOT / "config/event_graph_relation_candidates_v001.json"
)


def audit(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    db = ROOT / cfg["output_db"]
    if not db.is_file():
        raise FileNotFoundError(db)

    failures = []
    reviews = []

    with sqlite3.connect(db) as conn:
        run = conn.execute(
            """
            SELECT status,documents_scanned,candidates_written
            FROM extraction_runs
            ORDER BY rowid DESC LIMIT 1
            """
        ).fetchone()
        total = conn.execute(
            "SELECT COUNT(*) FROM relation_name_candidates"
        ).fetchone()[0]
        by_relation = dict(conn.execute(
            """
            SELECT relation_type,COUNT(*)
            FROM relation_name_candidates
            GROUP BY relation_type
            ORDER BY relation_type
            """
        ).fetchall())
        by_method = dict(conn.execute(
            """
            SELECT extraction_method,COUNT(*)
            FROM relation_name_candidates
            GROUP BY extraction_method
            ORDER BY extraction_method
            """
        ).fetchall())
        by_resolution = dict(conn.execute(
            """
            SELECT resolution_status,COUNT(*)
            FROM relation_name_candidates
            GROUP BY resolution_status
            ORDER BY resolution_status
            """
        ).fetchall())
        candidate_docs = conn.execute(
            """
            SELECT COUNT(DISTINCT corpus_document_id)
            FROM relation_name_candidates
            """
        ).fetchone()[0]
        source_entities = conn.execute(
            """
            SELECT COUNT(DISTINCT source_entity_id)
            FROM relation_name_candidates
            """
        ).fetchone()[0]
        target_names = conn.execute(
            """
            SELECT COUNT(DISTINCT target_name_normalized)
            FROM relation_name_candidates
            """
        ).fetchone()[0]
        bad_pit = conn.execute(
            """
            SELECT COUNT(*)
            FROM relation_name_candidates
            WHERE availability_is_point_in_time<>0
            """
        ).fetchone()[0]
        bad_evidence = conn.execute(
            """
            SELECT COUNT(*)
            FROM relation_name_candidates
            WHERE evidence_char_end<=evidence_char_start
               OR TRIM(evidence_text)=''
               OR raw_sha256 IS NULL
               OR LENGTH(raw_sha256)<>64
            """
        ).fetchone()[0]
        self_rel = conn.execute(
            """
            SELECT COUNT(*)
            FROM relation_name_candidates
            WHERE resolved_target_entity_id=source_entity_id
            """
        ).fetchone()[0]
        duplicate = conn.execute(
            """
            SELECT COUNT(*)
            FROM (
              SELECT
                corpus_document_id,source_entity_id,relation_type,
                target_name_normalized,COUNT(*) n
              FROM relation_name_candidates
              GROUP BY
                corpus_document_id,source_entity_id,relation_type,
                target_name_normalized
              HAVING n>1
            )
            """
        ).fetchone()[0]
        graphish_tables = {
            str(r[0])
            for r in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            if "assertion" in str(r[0]).lower()
            or "edge" in str(r[0]).lower()
            or "propagation" in str(r[0]).lower()
        }

    if run is None or run[0] != "completed":
        failures.append("extraction_run_not_completed")
    if total == 0:
        failures.append("zero_relation_candidates")
    if bad_pit:
        failures.append("historical_candidate_incorrectly_marked_pit")
    if bad_evidence:
        failures.append("candidate_missing_valid_evidence_span")
    if self_rel:
        failures.append("self_relation_candidate_present")
    if duplicate:
        failures.append("duplicate_candidate_key_present")
    if graphish_tables:
        failures.append("candidate_db_contains_graph_or_assertion_tables")

    if by_relation.get("parent_of", 0) == 0:
        failures.append("zero_exhibit21_parent_candidates")
    if by_relation.get("contract_party_of", 0) == 0:
        reviews.append("zero_contract_party_candidates")
    unresolved = int(by_resolution.get("unresolved", 0))
    if unresolved > 0:
        reviews.append("unresolved_target_entities_expected_before_resolution")

    status = "FAIL" if failures else ("REVIEW" if reviews else "PASS")
    return {
        "status": status,
        "failures": failures,
        "reviews": reviews,
        "candidates": int(total),
        "candidate_documents": int(candidate_docs),
        "source_entities": int(source_entities),
        "unique_target_names": int(target_names),
        "by_relation_type": by_relation,
        "by_extraction_method": by_method,
        "by_resolution_status": by_resolution,
        "causal_contract": {
            "strict_historical_pit": False,
            "all_candidates_pit_zero": bad_pit == 0,
            "evidence_span_required": True,
            "future_evidence_created": False,
        },
        "graph_contract": {
            "candidate_db_has_graph_edges": bool(graphish_tables),
            "relations_promoted": False,
            "market_direction_assigned": False,
            "market_weight_assigned": False,
        },
        "next_gate": (
            "Generate deterministic QA sample and manually review precision. "
            "Do not promote candidates or expand to semantic supplier/customer "
            "extraction until the high-precision V001 sample is reviewed."
        ),
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    a = p.parse_args()
    print(json.dumps(audit(a.config), indent=2))

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/event_graph_entity_identity_audit_v001.json"
DEFAULT_REPORT = (
    ROOT / "reports/event_graph/entity_identity_v001/conflict_evidence.json"
)


def review(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(config_path.read_text())
    identity_db = ROOT / cfg["output_db"]
    registry_db = ROOT / cfg["registry_db"]

    if not identity_db.is_file():
        raise FileNotFoundError(identity_db)
    if not registry_db.is_file():
        raise FileNotFoundError(registry_db)

    with sqlite3.connect(
        f"file:{identity_db.resolve()}?mode=ro", uri=True
    ) as ident:
        pairs = ident.execute(
            """
            SELECT
              identity_candidate_id,
              left_registry_name_id,
              right_registry_name_id,
              left_display_name,
              right_display_name,
              candidate_kind,
              canonical_identity_key,
              shared_registrant_ids_json,
              shared_accession_count,
              temporal_relation
            FROM identity_candidate_pairs
            WHERE same_accession_cooccurrence<>0
            ORDER BY identity_candidate_id
            """
        ).fetchall()

    output = []
    with sqlite3.connect(
        f"file:{registry_db.resolve()}?mode=ro", uri=True
    ) as reg:
        for row in pairs:
            (
                cid, left_id, right_id, left_name, right_name, kind, key,
                shared_regs_json, shared_accession_count, temporal
            ) = row

            left_accessions = {
                str(x[0])
                for x in reg.execute(
                    """
                    SELECT accession_number
                    FROM registry_name_evidence
                    WHERE registry_name_id=?
                    """,
                    (left_id,),
                ).fetchall()
            }
            right_accessions = {
                str(x[0])
                for x in reg.execute(
                    """
                    SELECT accession_number
                    FROM registry_name_evidence
                    WHERE registry_name_id=?
                    """,
                    (right_id,),
                ).fetchall()
            }
            shared = sorted(left_accessions & right_accessions)

            shared_evidence = []
            for accession in shared:
                side_payload = {}
                for side, name_id in (
                    ("left", left_id),
                    ("right", right_id),
                ):
                    rows_ev = reg.execute(
                        """
                        SELECT
                          evidence_claim_id,
                          registrant_ticker,
                          raw_name,
                          evidence_available_at,
                          evidence_text,
                          raw_sha256,
                          source_url
                        FROM registry_name_evidence
                        WHERE registry_name_id=?
                          AND accession_number=?
                        ORDER BY evidence_available_at,evidence_claim_id
                        """,
                        (name_id, accession),
                    ).fetchall()
                    side_payload[side] = [
                        {
                            "evidence_claim_id": str(ev[0]),
                            "registrant_ticker": ev[1],
                            "raw_name": str(ev[2]),
                            "evidence_available_at": str(ev[3]),
                            "evidence_text": str(ev[4]),
                            "raw_sha256": str(ev[5]),
                            "source_url": ev[6],
                        }
                        for ev in rows_ev
                    ]

                shared_evidence.append(
                    {
                        "accession_number": accession,
                        "left": side_payload["left"],
                        "right": side_payload["right"],
                    }
                )

            output.append(
                {
                    "identity_candidate_id": str(cid),
                    "left_display_name": str(left_name),
                    "right_display_name": str(right_name),
                    "candidate_kind": str(kind),
                    "canonical_identity_key": str(key),
                    "shared_registrant_ids": json.loads(shared_regs_json),
                    "shared_accession_count_reported":
                        int(shared_accession_count),
                    "shared_accession_count_reconstructed": len(shared),
                    "temporal_relation": str(temporal),
                    "shared_accession_evidence": shared_evidence,
                    "automatic_merge_allowed": False,
                }
            )

    count_mismatch = [
        x["identity_candidate_id"]
        for x in output
        if x["shared_accession_count_reported"]
        != x["shared_accession_count_reconstructed"]
    ]

    return {
        "status": "FAIL" if count_mismatch else "PASS",
        "failures": (
            ["shared_accession_count_mismatch"] if count_mismatch else []
        ),
        "conflict_pairs": len(output),
        "count_mismatch_candidate_ids": count_mismatch,
        "pairs": output,
        "main_db_mutated": False,
        "canonical_entities_created": False,
        "automatic_merge_allowed": False,
        "graph_edges_written": False,
        "next_gate": (
            "Manually classify these conflict pairs using exact evidence. "
            "All other V001 candidate pairs remain identity hypotheses, not merges."
        ),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    a = p.parse_args()

    result = review(a.config)
    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(
        json.dumps(result, indent=2) + "\n",
        encoding="utf-8",
    )
    print(json.dumps({
        "status": result["status"],
        "conflict_pairs": result["conflict_pairs"],
        "failures": result["failures"],
        "report": str(a.report),
        "main_db_mutated": False,
        "automatic_merge_allowed": False,
    }, indent=2))


if __name__ == "__main__":
    main()

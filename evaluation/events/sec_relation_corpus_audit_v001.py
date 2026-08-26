from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/event_graph_sec_relation_corpus_v001.json"


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
            SELECT status,documents_written,chunks_written
            FROM corpus_runs
            ORDER BY rowid DESC LIMIT 1
            """
        ).fetchone()
        docs = conn.execute(
            "SELECT COUNT(*) FROM corpus_documents"
        ).fetchone()[0]
        chunks = conn.execute(
            "SELECT COUNT(*) FROM corpus_chunks"
        ).fetchone()[0]
        assets = conn.execute(
            """
            SELECT COUNT(DISTINCT asset_id)
            FROM corpus_documents WHERE asset_id IS NOT NULL
            """
        ).fetchone()[0]
        entities = conn.execute(
            """
            SELECT COUNT(DISTINCT subject_entity_id)
            FROM corpus_documents
            WHERE subject_entity_id IS NOT NULL
            """
        ).fetchone()[0]
        filings = conn.execute(
            """
            SELECT COUNT(DISTINCT accession_number)
            FROM corpus_documents
            """
        ).fetchone()[0]
        missing_asset = conn.execute(
            """
            SELECT COUNT(*) FROM corpus_documents
            WHERE asset_id IS NULL
            """
        ).fetchone()[0]
        missing_entity = conn.execute(
            """
            SELECT COUNT(*) FROM corpus_documents
            WHERE subject_entity_id IS NULL
            """
        ).fetchone()[0]
        pit_rows = conn.execute(
            """
            SELECT COUNT(*) FROM corpus_documents
            WHERE availability_is_point_in_time<>0
            """
        ).fetchone()[0]
        duplicate_docs = conn.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT content_raw_document_id,asset_id,COUNT(*) n
              FROM corpus_documents
              GROUP BY content_raw_document_id,asset_id
              HAVING n>1
            )
            """
        ).fetchone()[0]
        orphan_chunks = conn.execute(
            """
            SELECT COUNT(*)
            FROM corpus_chunks c
            LEFT JOIN corpus_documents d
              ON d.corpus_document_id=c.corpus_document_id
            WHERE d.corpus_document_id IS NULL
            """
        ).fetchone()[0]
        classes = dict(conn.execute(
            """
            SELECT document_class,COUNT(*)
            FROM corpus_documents
            GROUP BY document_class
            ORDER BY document_class
            """
        ).fetchall())
        forms = dict(conn.execute(
            """
            SELECT form,COUNT(*)
            FROM corpus_documents
            GROUP BY form
            ORDER BY form
            """
        ).fetchall())
        cue_totals = {}
        for (raw_json,) in conn.execute(
            "SELECT cue_counts_json FROM corpus_documents"
        ):
            for k, v in json.loads(raw_json).items():
                cue_totals[k] = cue_totals.get(k, 0) + int(v)

    if run is None or run[0] != "completed":
        failures.append("corpus_run_not_completed")
    if docs == 0 or chunks == 0:
        failures.append("empty_corpus")
    if pit_rows:
        failures.append("historical_corpus_incorrectly_marked_strict_pit")
    if duplicate_docs:
        failures.append("duplicate_corpus_documents")
    if orphan_chunks:
        failures.append("orphan_corpus_chunks")
    if missing_asset:
        reviews.append("documents_without_asset_link")
    if missing_entity:
        reviews.append("documents_without_subject_entity")
    if assets < 10:
        reviews.append("narrow_asset_coverage")

    status = "FAIL" if failures else ("REVIEW" if reviews else "PASS")
    return {
        "status": status,
        "failures": failures,
        "reviews": reviews,
        "documents": int(docs),
        "chunks": int(chunks),
        "filings": int(filings),
        "assets": int(assets),
        "subject_entities": int(entities),
        "document_class_counts": classes,
        "form_counts": forms,
        "cue_totals": dict(sorted(cue_totals.items())),
        "causal_contract": {
            "strict_historical_pit": False,
            "all_corpus_rows_pit_zero": pit_rows == 0,
            "effective_availability_uses_document_and_metadata_clocks": True,
            "future_relation_evidence_not_created": True,
        },
        "graph_contract": {
            "relation_candidates_written": False,
            "graph_edges_written": False,
            "market_direction_assigned": False,
            "market_weight_assigned": False,
        },
        "next_gate": (
            "Review corpus coverage and cue yield. If healthy, implement "
            "high-precision structural relation candidate extraction with "
            "entity resolution; candidates remain non-model-visible."
        ),
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    a = p.parse_args()
    print(json.dumps(audit(a.config), indent=2))

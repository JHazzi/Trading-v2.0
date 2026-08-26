from __future__ import annotations
import argparse
import json
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/event_graph_ex21_structured_rows_v001.json"


def audit(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(config_path.read_text())
    db = ROOT / cfg["output_db"]
    failures = []
    reviews = []

    with sqlite3.connect(db) as c:
        run = c.execute(
            "SELECT status,documents_scanned,documents_with_rows,rows_written "
            "FROM extraction_runs ORDER BY rowid DESC LIMIT 1"
        ).fetchone()
        docs = c.execute(
            "SELECT COUNT(*) FROM structured_documents"
        ).fetchone()[0]
        docs_rows = c.execute(
            "SELECT COUNT(*) FROM structured_documents "
            "WHERE data_table_count>0"
        ).fetchone()[0]
        rows = c.execute(
            "SELECT COUNT(*) FROM structured_ex21_rows"
        ).fetchone()[0]
        tickers = c.execute(
            "SELECT COUNT(DISTINCT registrant_ticker) "
            "FROM structured_ex21_rows"
        ).fetchone()[0]
        missing_name = c.execute(
            "SELECT COUNT(*) FROM structured_ex21_rows "
            "WHERE TRIM(legal_name_clean)=''"
        ).fetchone()[0]
        bad_pit = c.execute(
            "SELECT COUNT(*) FROM structured_ex21_rows "
            "WHERE availability_is_point_in_time<>0"
        ).fetchone()[0]
        by_family = dict(c.execute(
            "SELECT schema_family,COUNT(*) FROM structured_ex21_rows "
            "GROUP BY schema_family ORDER BY schema_family"
        ).fetchall())

        # sqlite returns one row containing six scalar aggregates.
        # The previous implementation incorrectly wrapped this tuple in
        # dict(...), which raises TypeError because the elements are scalars,
        # not key/value pairs.
        doc_schema_row = c.execute(
            """SELECT
               SUM(CASE WHEN explicit_schema_tables>0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN implicit_schema_tables>0 THEN 1 ELSE 0 END),
               SUM(CASE WHEN inherited_schema_tables>0 THEN 1 ELSE 0 END),
               SUM(footnote_tables_skipped),
               SUM(uniform_format_span_tables),
               SUM(unsupported_tables)
               FROM structured_documents"""
        ).fetchone()
        doc_schema = tuple(doc_schema_row or (0, 0, 0, 0, 0, 0))
        if len(doc_schema) != 6:
            raise RuntimeError(
                f"unexpected document schema aggregate width: {len(doc_schema)}"
            )

        dba_rows = c.execute(
            "SELECT COUNT(*) FROM structured_ex21_rows "
            "WHERE dba_alias_raw IS NOT NULL"
        ).fetchone()[0]
        jurisdiction_rows = c.execute(
            "SELECT COUNT(*) FROM structured_ex21_rows "
            "WHERE jurisdiction_raw IS NOT NULL"
        ).fetchone()[0]
        ownership_rows = c.execute(
            "SELECT COUNT(*) FROM structured_ex21_rows "
            "WHERE ownership_raw IS NOT NULL"
        ).fetchone()[0]
        footnote_ref_rows = c.execute(
            "SELECT COUNT(*) FROM structured_ex21_rows "
            "WHERE legal_name_footnote_refs_json<>'[]'"
        ).fetchone()[0]
        graphish = [
            r[0]
            for r in c.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
            if any(
                x in r[0].lower()
                for x in ("edge", "assertion", "propagation")
            )
        ]

    if run is None or run[0] != "completed":
        failures.append("extraction_run_not_completed")
    if docs == 0 or rows == 0:
        failures.append("empty_structured_ex21_output")
    if missing_name:
        failures.append("empty_legal_name_rows")
    if bad_pit:
        failures.append("historical_row_incorrectly_marked_pit")
    if graphish:
        failures.append("structured_row_db_contains_graph_tables")
    if docs_rows < docs:
        reviews.append("some_ex21_documents_have_no_structured_rows")
    if tickers < 10:
        reviews.append("structured_rows_missing_some_seed_tickers")
    if rows and jurisdiction_rows / rows < 0.85:
        reviews.append("jurisdiction_coverage_below_85_percent")

    status = "FAIL" if failures else ("REVIEW" if reviews else "PASS")
    return {
        "status": status,
        "failures": failures,
        "reviews": reviews,
        "documents": int(docs),
        "documents_with_data_tables": int(docs_rows),
        "rows": int(rows),
        "tickers": int(tickers),
        "by_schema_family": by_family,
        "document_schema_summary": {
            "documents_with_explicit_schema": int(doc_schema[0] or 0),
            "documents_with_implicit_schema": int(doc_schema[1] or 0),
            "documents_with_inherited_schema": int(doc_schema[2] or 0),
            "footnote_tables_skipped": int(doc_schema[3] or 0),
            "uniform_format_span_tables": int(doc_schema[4] or 0),
            "unsupported_tables": int(doc_schema[5] or 0),
        },
        "field_coverage": {
            "jurisdiction_rows": int(jurisdiction_rows),
            "jurisdiction_fraction": jurisdiction_rows / rows if rows else 0,
            "dba_rows": int(dba_rows),
            "ownership_rows": int(ownership_rows),
            "rows_with_trailing_footnote_refs": int(footnote_ref_rows),
        },
        "causal_contract": {
            "strict_historical_pit": False,
            "all_rows_pit_zero": bad_pit == 0,
        },
        "identity_contract": {
            "canonical_entities_created": False,
            "identity_merges_performed": False,
        },
        "graph_contract": {
            "graph_tables_present": graphish,
            "graph_edges_written": False,
            "relation_promotion": False,
        },
        "next_gate": (
            "Review stratified QA rows. If row/column alignment is precise, "
            "build Entity Registry V2 from structured legal-name + jurisdiction "
            "+ DBA + ownership evidence. Do not create canonical entities yet."
        ),
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    a = p.parse_args()
    print(json.dumps(audit(a.config), indent=2))

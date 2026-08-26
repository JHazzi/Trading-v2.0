import json
import sqlite3
from pathlib import Path

from evaluation.events.ex21_structured_rows_audit_v001 import audit


def make_db(path: Path):
    with sqlite3.connect(path) as c:
        c.executescript("""
        CREATE TABLE extraction_runs(
          status TEXT,
          documents_scanned INTEGER,
          documents_with_rows INTEGER,
          rows_written INTEGER
        );
        INSERT INTO extraction_runs VALUES ('completed',99,99,8111);

        CREATE TABLE structured_documents(
          data_table_count INTEGER,
          explicit_schema_tables INTEGER,
          implicit_schema_tables INTEGER,
          inherited_schema_tables INTEGER,
          footnote_tables_skipped INTEGER,
          uniform_format_span_tables INTEGER,
          unsupported_tables INTEGER
        );
        INSERT INTO structured_documents VALUES (1,1,0,0,1,0,0);
        INSERT INTO structured_documents VALUES (1,0,1,0,0,1,2);
        INSERT INTO structured_documents VALUES (1,0,0,1,0,0,0);

        CREATE TABLE structured_ex21_rows(
          legal_name_clean TEXT,
          availability_is_point_in_time INTEGER,
          schema_family TEXT,
          registrant_ticker TEXT,
          jurisdiction_raw TEXT,
          dba_alias_raw TEXT,
          ownership_raw TEXT,
          legal_name_footnote_refs_json TEXT
        );
        """)

        tickers = [
          'AAPL','BAC','COST','CVX','JNJ',
          'JPM','LLY','MSFT','WMT','XOM'
        ]
        families = [
          'explicit_header','implicit_legal_name','inherited_schema'
        ]
        for i in range(100):
            c.execute(
                """INSERT INTO structured_ex21_rows VALUES
                (?,?,?,?,?,?,?,?)""",
                (
                    f"Entity {i}",
                    0,
                    families[i % 3],
                    tickers[i % 10],
                    "Delaware" if i < 90 else None,
                    "DBA" if i == 0 else None,
                    "100" if i < 10 else None,
                    "[5]" if i == 1 else "[]",
                ),
            )
        c.commit()


def test_audit_aggregate_row_is_tuple_not_dict(tmp_path):
    db = tmp_path / "rows.db"
    make_db(db)
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"output_db": str(db)}))

    result = audit(cfg)
    assert result["status"] == "PASS"
    assert result["documents"] == 3
    assert result["documents_with_data_tables"] == 3
    assert result["rows"] == 100
    assert result["tickers"] == 10
    assert result["document_schema_summary"] == {
        "documents_with_explicit_schema": 1,
        "documents_with_implicit_schema": 1,
        "documents_with_inherited_schema": 1,
        "footnote_tables_skipped": 1,
        "uniform_format_span_tables": 1,
        "unsupported_tables": 2,
    }
    assert result["field_coverage"]["jurisdiction_fraction"] == 0.9


def test_audit_never_claims_identity_or_graph_promotion(tmp_path):
    db = tmp_path / "rows.db"
    make_db(db)
    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"output_db": str(db)}))

    result = audit(cfg)
    assert result["identity_contract"]["canonical_entities_created"] is False
    assert result["identity_contract"]["identity_merges_performed"] is False
    assert result["graph_contract"]["graph_edges_written"] is False
    assert result["graph_contract"]["relation_promotion"] is False

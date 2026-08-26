from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import re
import sqlite3
from collections import Counter
from dataclasses import dataclass, asdict
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/event_graph_ex21_structure_audit_v001.json"


def clean_text(value: str) -> str:
    text = html.unescape(str(value or ""))
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text


def norm_header(value: str) -> str:
    text = clean_text(value).casefold()
    text = re.sub(r"[^a-z0-9]+", " ", text)
    return re.sub(r"\s+", " ", text).strip()


@dataclass
class Cell:
    text: str
    tag: str
    rowspan: int
    colspan: int


class TableParser(HTMLParser):
    """
    Minimal deterministic HTML table parser.

    We keep original row/cell boundaries and rowspan/colspan metadata. We do
    not try to expand spans in this audit because the purpose is to determine
    whether that can be done safely in the next extractor.
    """

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[Cell]]] = []
        self._table_depth = 0
        self._current_table: list[list[Cell]] | None = None
        self._current_row: list[Cell] | None = None
        self._cell_tag: str | None = None
        self._cell_parts: list[str] = []
        self._cell_rowspan = 1
        self._cell_colspan = 1
        self._drop_depth = 0

    def handle_starttag(self, tag, attrs):
        t = tag.casefold()
        amap = {str(k).casefold(): v for k, v in attrs}

        if t in {"script", "style", "noscript", "svg"}:
            self._drop_depth += 1
            return
        if self._drop_depth:
            return

        if t == "table":
            self._table_depth += 1
            if self._table_depth == 1:
                self._current_table = []
            return

        if self._table_depth != 1:
            return

        if t == "tr":
            self._current_row = []
        elif t in {"td", "th"} and self._current_row is not None:
            self._cell_tag = t
            self._cell_parts = []
            try:
                self._cell_rowspan = max(1, int(amap.get("rowspan") or 1))
            except Exception:
                self._cell_rowspan = 1
            try:
                self._cell_colspan = max(1, int(amap.get("colspan") or 1))
            except Exception:
                self._cell_colspan = 1
        elif t == "br" and self._cell_tag is not None:
            self._cell_parts.append("\n")

    def handle_data(self, data):
        if self._drop_depth or self._table_depth != 1:
            return
        if self._cell_tag is not None:
            self._cell_parts.append(data)

    def handle_endtag(self, tag):
        t = tag.casefold()
        if t in {"script", "style", "noscript", "svg"}:
            if self._drop_depth:
                self._drop_depth -= 1
            return
        if self._drop_depth:
            return

        if t in {"td", "th"} and self._cell_tag == t:
            assert self._current_row is not None
            self._current_row.append(
                Cell(
                    text=clean_text(" ".join(self._cell_parts)),
                    tag=t,
                    rowspan=self._cell_rowspan,
                    colspan=self._cell_colspan,
                )
            )
            self._cell_tag = None
            self._cell_parts = []
            self._cell_rowspan = 1
            self._cell_colspan = 1
        elif t == "tr" and self._table_depth == 1:
            if self._current_table is not None and self._current_row is not None:
                if any(c.text for c in self._current_row):
                    self._current_table.append(self._current_row)
            self._current_row = None
        elif t == "table":
            if self._table_depth == 1:
                if self._current_table:
                    self.tables.append(self._current_table)
                self._current_table = None
            if self._table_depth:
                self._table_depth -= 1


def decode_payload(storage_bytes: bytes, content_encoding: str | None) -> bytes:
    enc = (content_encoding or "").casefold()
    if storage_bytes[:2] == b"\x1f\x8b" or "gzip" in enc:
        return gzip.decompress(storage_bytes)
    return storage_bytes


def resolve_storage_path(value: str) -> Path:
    p = Path(str(value))
    return p if p.is_absolute() else ROOT / p


def read_docs(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          corpus_document_id,
          content_raw_document_id,
          accession_number,
          asset_id,
          subject_entity_id,
          asset_ticker,
          source_url,
          raw_sha256,
          storage_path,
          content_type,
          content_encoding,
          source_byte_length,
          effective_available_at
        FROM corpus_documents
        WHERE document_class='subsidiary_exhibit'
        ORDER BY effective_available_at,accession_number,corpus_document_id
        """
    ).fetchall()
    names = [
        "corpus_document_id","content_raw_document_id","accession_number",
        "asset_id","subject_entity_id","asset_ticker","source_url",
        "raw_sha256","storage_path","content_type","content_encoding",
        "source_byte_length","effective_available_at",
    ]
    return [dict(zip(names, r)) for r in rows]


def role_for_header(text: str, cfg: dict) -> set[str]:
    n = norm_header(text)
    if not n:
        return set()

    roles = set()
    for role, patterns in cfg["column_roles"].items():
        for pattern in patterns:
            p = norm_header(pattern)
            if not p:
                continue
            if n == p or p in n:
                roles.add(role)
                break
    return roles


def table_signature(table: list[list[Cell]], cfg: dict) -> dict[str, Any]:
    max_rows = min(
        len(table),
        int(cfg["header_detection"]["max_header_rows"]),
    )
    candidates = []

    for row_idx in range(max_rows):
        row = table[row_idx]
        cell_roles = []
        recognized = set()
        for col_idx, cell in enumerate(row):
            roles = sorted(role_for_header(cell.text, cfg))
            recognized.update(roles)
            cell_roles.append({
                "column_index": col_idx,
                "text": cell.text,
                "roles": roles,
                "tag": cell.tag,
                "rowspan": cell.rowspan,
                "colspan": cell.colspan,
            })

        qualifies = (
            len(recognized)
            >= int(cfg["header_detection"]["minimum_recognized_roles"])
            and (
                not cfg["header_detection"]["require_legal_name_role"]
                or "legal_name" in recognized
            )
        )
        candidates.append({
            "row_index": row_idx,
            "recognized_roles": sorted(recognized),
            "cells": cell_roles,
            "qualifies": bool(qualifies),
        })

    qualifying = [x for x in candidates if x["qualifies"]]
    chosen = qualifying[0] if qualifying else None

    all_spans = [
        c for row in table for c in row
        if c.rowspan > 1 or c.colspan > 1
    ]

    return {
        "rows": len(table),
        "max_cells_in_row": max((len(r) for r in table), default=0),
        "span_cells": len(all_spans),
        "header_candidates": candidates,
        "chosen_header": chosen,
        "structured_candidate": chosen is not None,
    }


def inspect_document(row: dict[str, Any], cfg: dict) -> dict[str, Any]:
    path = resolve_storage_path(row["storage_path"])
    result = {
        "corpus_document_id": row["corpus_document_id"],
        "accession_number": row["accession_number"],
        "ticker": row["asset_ticker"],
        "source_url": row["source_url"],
        "effective_available_at": row["effective_available_at"],
        "storage_path": str(row["storage_path"]),
        "status": "PASS",
        "failures": [],
        "content_kind": None,
        "tables": 0,
        "structured_candidate_tables": 0,
        "table_summaries": [],
    }

    if not path.is_file():
        result["status"] = "FAIL"
        result["failures"].append("storage_missing")
        return result

    storage = path.read_bytes()
    payload = decode_payload(storage, row["content_encoding"])
    actual_sha = hashlib.sha256(payload).hexdigest()
    if actual_sha != str(row["raw_sha256"]):
        result["status"] = "FAIL"
        result["failures"].append("raw_sha256_mismatch")
        return result

    ctype = (row["content_type"] or "").casefold()
    prefix = payload[:5000].lower()
    looks_html = (
        "html" in ctype
        or b"<html" in prefix
        or b"<table" in prefix
        or b"<body" in prefix
    )
    result["content_kind"] = "html" if looks_html else "text"

    if not looks_html:
        return result

    text = payload.decode("utf-8", errors="replace")
    parser = TableParser()
    try:
        parser.feed(text)
    except Exception as exc:
        result["status"] = "FAIL"
        result["failures"].append(
            f"html_parse_error:{type(exc).__name__}"
        )
        return result

    result["tables"] = len(parser.tables)
    for i, table in enumerate(parser.tables):
        sig = table_signature(table, cfg)
        if sig["structured_candidate"]:
            result["structured_candidate_tables"] += 1

        # Preserve only compact examples in the audit report.
        preview = []
        for r in table[:8]:
            preview.append([
                {
                    "text": c.text[:300],
                    "tag": c.tag,
                    "rowspan": c.rowspan,
                    "colspan": c.colspan,
                }
                for c in r[:8]
            ])

        result["table_summaries"].append({
            "table_index": i,
            **sig,
            "preview_rows": preview,
        })

    return result


def plan(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    db = ROOT / cfg["corpus_db"]
    if not db.is_file():
        raise FileNotFoundError(db)

    with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as conn:
        docs = read_docs(conn)

    missing = []
    total_bytes = 0
    for row in docs:
        total_bytes += int(row["source_byte_length"] or 0)
        path = resolve_storage_path(row["storage_path"])
        if not path.is_file():
            missing.append({
                "corpus_document_id": row["corpus_document_id"],
                "storage_path": row["storage_path"],
            })

    return {
        "status": "FAIL" if missing else "PASS",
        "failures": ["selected_storage_missing"] if missing else [],
        "selected_ex21_documents": len(docs),
        "selected_bytes": total_bytes,
        "missing_storage_count": len(missing),
        "missing_storage_examples": missing[:20],
        "strict_historical_pit": False,
        "main_db_mutated": False,
        "canonical_entities_created": False,
        "graph_edges_written": False,
        "next_gate": (
            "If PASS, run the read-only structure audit over raw EX-21 payloads."
        ),
    }


def audit(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    pre = plan(config_path)
    if pre["status"] != "PASS":
        raise RuntimeError(pre["failures"])

    db = ROOT / cfg["corpus_db"]
    with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as conn:
        docs = read_docs(conn)

    results = [inspect_document(row, cfg) for row in docs]
    failures = [
        {
            "corpus_document_id": x["corpus_document_id"],
            "failures": x["failures"],
        }
        for x in results
        if x["status"] == "FAIL"
    ]

    html_docs = [x for x in results if x["content_kind"] == "html"]
    text_docs = [x for x in results if x["content_kind"] == "text"]
    docs_with_tables = [x for x in html_docs if x["tables"] > 0]
    structured_docs = [
        x for x in html_docs if x["structured_candidate_tables"] > 0
    ]

    header_patterns = Counter()
    role_sets = Counter()
    span_docs = 0
    for doc in results:
        doc_has_span = False
        for table in doc["table_summaries"]:
            chosen = table["chosen_header"]
            if chosen:
                header_patterns[
                    " | ".join(
                        c["text"] for c in chosen["cells"] if c["text"]
                    )[:500]
                ] += 1
                role_sets[
                    ",".join(chosen["recognized_roles"])
                ] += 1
            if table["span_cells"] > 0:
                doc_has_span = True
        if doc_has_span:
            span_docs += 1

    coverage = (
        len(structured_docs) / len(results)
        if results else 0.0
    )

    reviews = []
    if text_docs:
        reviews.append("non_html_ex21_documents_require_fallback")
    if coverage < 0.80:
        reviews.append("structured_table_coverage_below_80_percent")
    if span_docs:
        reviews.append("rowspan_colspan_present_requires_expansion_logic")

    status = "FAIL" if failures else ("REVIEW" if reviews else "PASS")

    report_dir = ROOT / cfg["report_dir"]
    report_dir.mkdir(parents=True, exist_ok=True)
    detail_path = report_dir / "document_details.json"
    detail_path.write_text(
        json.dumps(results, indent=2) + "\n",
        encoding="utf-8",
    )

    summary = {
        "status": status,
        "failures": failures,
        "reviews": reviews,
        "documents": len(results),
        "html_documents": len(html_docs),
        "text_documents": len(text_docs),
        "documents_with_tables": len(docs_with_tables),
        "documents_with_structured_candidate_table": len(structured_docs),
        "structured_candidate_document_fraction": coverage,
        "documents_with_rowspan_or_colspan": span_docs,
        "top_header_patterns": [
            {"pattern": k, "documents": v}
            for k, v in header_patterns.most_common(20)
        ],
        "recognized_role_sets": dict(role_sets.most_common()),
        "source_integrity": {
            "all_raw_sha256_verified": not failures,
            "main_db_mutated": False,
        },
        "identity_contract": {
            "canonical_entities_created": False,
            "identity_merges_performed": False,
            "jurisdiction_used_for_identity_yet": False,
        },
        "graph_contract": {
            "graph_edges_written": False,
            "relation_promotion": False,
        },
        "detail_report": str(detail_path),
        "next_gate": cfg["next_gate"],
    }

    summary_path = report_dir / "audit.json"
    summary_path.write_text(
        json.dumps(summary, indent=2) + "\n",
        encoding="utf-8",
    )
    return summary


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--stage", required=True, choices=("plan", "audit"))
    a = p.parse_args()
    result = plan(a.config) if a.stage == "plan" else audit(a.config)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

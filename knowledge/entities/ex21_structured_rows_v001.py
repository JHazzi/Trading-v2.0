from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from html.parser import HTMLParser
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = (
    ROOT / "config/event_graph_ex21_structured_rows_v001.json"
)


def clean_text(value: str) -> str:
    text = html.unescape(str(value or ""))
    text = text.replace("\u00a0", " ")
    return re.sub(r"\s+", " ", text).strip()


def norm_header(value: str) -> str:
    text = clean_text(value).casefold()
    text = re.sub(r"[_–—-]+", " ", text)
    text = re.sub(r"\s+", " ", text)
    return text.strip(" \t\r\n:;.")


@dataclass(frozen=True)
class Cell:
    text: str
    tag: str
    rowspan: int
    colspan: int


class TableParser(HTMLParser):
    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.tables: list[list[list[Cell]]] = []
        self._depth = 0
        self._table: list[list[Cell]] | None = None
        self._row: list[Cell] | None = None
        self._cell_tag: str | None = None
        self._parts: list[str] = []
        self._rowspan = 1
        self._colspan = 1
        self._drop = 0

    def handle_starttag(self, tag, attrs):
        t = tag.casefold()
        amap = {str(k).casefold(): v for k, v in attrs}
        if t in {"script", "style", "noscript", "svg"}:
            self._drop += 1
            return
        if self._drop:
            return
        if t == "table":
            self._depth += 1
            if self._depth == 1:
                self._table = []
            return
        if self._depth != 1:
            return
        if t == "tr":
            self._row = []
        elif t in {"td", "th"} and self._row is not None:
            self._cell_tag = t
            self._parts = []
            try:
                self._rowspan = max(1, int(amap.get("rowspan") or 1))
            except Exception:
                self._rowspan = 1
            try:
                self._colspan = max(1, int(amap.get("colspan") or 1))
            except Exception:
                self._colspan = 1
        elif t == "br" and self._cell_tag is not None:
            self._parts.append("\n")

    def handle_data(self, data):
        if self._drop or self._depth != 1:
            return
        if self._cell_tag is not None:
            self._parts.append(data)

    def handle_endtag(self, tag):
        t = tag.casefold()
        if t in {"script", "style", "noscript", "svg"}:
            if self._drop:
                self._drop -= 1
            return
        if self._drop:
            return
        if t in {"td", "th"} and self._cell_tag == t:
            assert self._row is not None
            self._row.append(
                Cell(
                    clean_text(" ".join(self._parts)),
                    t,
                    self._rowspan,
                    self._colspan,
                )
            )
            self._cell_tag = None
            self._parts = []
            self._rowspan = 1
            self._colspan = 1
        elif t == "tr" and self._depth == 1:
            if self._table is not None and self._row is not None:
                if any(c.text for c in self._row):
                    self._table.append(self._row)
            self._row = None
        elif t == "table":
            if self._depth == 1:
                if self._table:
                    self.tables.append(self._table)
                self._table = None
            if self._depth:
                self._depth -= 1


@dataclass(frozen=True)
class Schema:
    family: str
    arity: int
    header_row_index: int
    role_to_col: dict[str, int]
    inferred_roles: tuple[str, ...]


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg["version"] != "event_graph_ex21_structured_rows_v001":
        raise ValueError("unexpected config version")
    return cfg


def role_for_header(text: str, cfg: dict) -> set[str]:
    n = norm_header(text)
    if not n:
        return set()
    if len(n) > int(cfg["header_rules"]["max_header_cell_chars"]):
        return set()

    out = set()
    mapping = {
        "legal_name": cfg["header_rules"]["legal_name_patterns"],
        "jurisdiction": cfg["header_rules"]["jurisdiction_patterns"],
        "location": cfg["header_rules"]["location_patterns"],
        "dba_alias": cfg["header_rules"]["dba_alias_patterns"],
        "ownership": cfg["header_rules"]["ownership_patterns"],
    }
    for role, patterns in mapping.items():
        for pattern in patterns:
            if re.fullmatch(pattern, n, flags=re.I):
                out.add(role)
                break
    return out


def is_footnote_table(table: list[list[Cell]]) -> bool:
    if not table:
        return True
    # Strong SEC footnote pattern: numbered marker plus long prose.
    foot_rows = 0
    prose_rows = 0
    for row in table[:8]:
        texts = [c.text for c in row if c.text]
        if not texts:
            continue
        if re.fullmatch(r"\(?\d+\)?", texts[0]):
            foot_rows += 1
        if max((len(x) for x in texts), default=0) > 180:
            prose_rows += 1
    if foot_rows >= 1 and prose_rows >= 1:
        return True
    # One-row narrative/disclosure table should never establish schema.
    if len(table) == 1 and max((len(c.text) for c in table[0]), default=0) > 160:
        return True
    return False


def uniform_formatting_span(table: list[list[Cell]]) -> bool:
    cells = [c for row in table for c in row]
    spanned = [c for c in cells if c.rowspan > 1 or c.colspan > 1]
    if not spanned:
        return False
    if len(spanned) != len(cells):
        return False
    signatures = {(c.rowspan, c.colspan) for c in cells}
    return len(signatures) == 1


def row_arity(table: list[list[Cell]]) -> int:
    counts = Counter(len(row) for row in table if row)
    if not counts:
        return 0
    return counts.most_common(1)[0][0]


def likely_data_value(text: str) -> bool:
    n = clean_text(text)
    if not n:
        return False
    if len(n) > 220:
        return False
    if re.fullmatch(r"\(?\d+\)?", n):
        return False
    return True


def likely_ownership_value(text: str) -> bool:
    n = clean_text(text)
    if not n:
        return False
    return bool(re.fullmatch(r"\d+(?:\.\d+)?%?", n))


def infer_schema(
    table: list[list[Cell]],
    cfg: dict,
    inherited_by_arity: dict[int, Schema] | None = None,
) -> Schema | None:
    if not table or is_footnote_table(table):
        return None

    max_scan = min(
        len(table),
        int(cfg["header_rules"]["max_header_rows_to_scan"]),
    )
    min_data = int(cfg["header_rules"]["minimum_data_rows_for_inference"])
    arity = row_arity(table)
    if arity < 2:
        return None

    # 1) Explicit header.
    for idx in range(max_scan):
        row = table[idx]
        if len(row) != arity:
            continue
        role_to_col: dict[str, int] = {}
        for col, cell in enumerate(row):
            for role in role_for_header(cell.text, cfg):
                # Reject ambiguous duplicate assignment in the same row.
                if role not in role_to_col:
                    role_to_col[role] = col
        if "legal_name" in role_to_col and any(
            r in role_to_col
            for r in ("jurisdiction", "ownership", "dba_alias")
        ):
            return Schema(
                family="explicit_header",
                arity=arity,
                header_row_index=idx,
                role_to_col=role_to_col,
                inferred_roles=(),
            )

    # 2) Explicit non-name roles + unlabeled legal-name column.
    for idx in range(max_scan):
        row = table[idx]
        if len(row) != arity:
            continue
        role_to_col: dict[str, int] = {}
        for col, cell in enumerate(row):
            for role in role_for_header(cell.text, cfg):
                if role not in role_to_col:
                    role_to_col[role] = col

        if "legal_name" in role_to_col:
            continue
        if not any(r in role_to_col for r in ("jurisdiction", "ownership")):
            continue

        # Candidate unlabeled columns are blank in header and populated in data.
        candidates = [
            col for col, cell in enumerate(row)
            if not cell.text.strip() and col not in role_to_col.values()
        ]
        for col in candidates:
            subsequent = [
                r for r in table[idx + 1 : idx + 1 + max(min_data + 3, 6)]
                if len(r) == arity
            ]
            if len(subsequent) < min_data:
                continue
            good_name = sum(
                likely_data_value(r[col].text)
                for r in subsequent
            )
            if good_name < min_data:
                continue

            # At least one explicit semantic column must also be populated.
            semantic_ok = False
            for role in ("jurisdiction", "ownership"):
                if role not in role_to_col:
                    continue
                rc = role_to_col[role]
                vals = [r[rc].text for r in subsequent]
                if role == "ownership":
                    semantic_ok = (
                        sum(likely_ownership_value(x) for x in vals)
                        >= min_data
                    )
                else:
                    semantic_ok = (
                        sum(likely_data_value(x) for x in vals)
                        >= min_data
                    )
                if semantic_ok:
                    break
            if not semantic_ok:
                continue

            role_to_col["legal_name"] = col
            return Schema(
                family="implicit_legal_name",
                arity=arity,
                header_row_index=idx,
                role_to_col=role_to_col,
                inferred_roles=("legal_name",),
            )

    # 3) Continuation table: inherit schema only inside same document and same
    # physical arity. First rows must look like data, not prose/header.
    if inherited_by_arity and arity in inherited_by_arity:
        inherited = inherited_by_arity[arity]
        first_rows = [r for r in table[:4] if len(r) == arity]
        if len(first_rows) >= min_data:
            name_col = inherited.role_to_col["legal_name"]
            if sum(
                likely_data_value(r[name_col].text)
                for r in first_rows
            ) >= min_data:
                return Schema(
                    family="inherited_schema",
                    arity=arity,
                    header_row_index=-1,
                    role_to_col=dict(inherited.role_to_col),
                    inferred_roles=tuple(
                        sorted(set(inherited.inferred_roles) | {"schema"})
                    ),
                )

    return None


TRAILING_FOOTNOTES = re.compile(
    r"(?P<base>.*?)"
    r"(?P<notes>(?:\s*\(\d+\))+)\s*$"
)


def split_trailing_footnotes(value: str) -> tuple[str, list[int]]:
    text = clean_text(value)
    m = TRAILING_FOOTNOTES.fullmatch(text)
    if not m:
        return text, []
    notes = [int(x) for x in re.findall(r"\((\d+)\)", m.group("notes"))]
    base = clean_text(m.group("base"))
    return base, notes


def parse_ownership(value: str) -> float | None:
    text = clean_text(value).rstrip("%")
    if not re.fullmatch(r"\d+(?:\.\d+)?", text):
        return None
    try:
        return float(text)
    except Exception:
        return None


def decode_payload(data: bytes, content_encoding: str | None) -> bytes:
    enc = (content_encoding or "").casefold()
    if data[:2] == b"\x1f\x8b" or "gzip" in enc:
        return gzip.decompress(data)
    return data


def resolve_storage(value: str) -> Path:
    p = Path(str(value))
    return p if p.is_absolute() else ROOT / p


def source_documents(conn: sqlite3.Connection) -> list[dict[str, Any]]:
    rows = conn.execute(
        """
        SELECT
          corpus_document_id,content_raw_document_id,accession_number,
          asset_id,subject_entity_id,asset_ticker,source_url,raw_sha256,
          storage_path,content_type,content_encoding,source_byte_length,
          effective_available_at,availability_is_point_in_time
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
        "availability_is_point_in_time",
    ]
    return [dict(zip(names, r)) for r in rows]


def plan(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = load_config(config_path)
    db = ROOT / cfg["corpus_db"]
    if not db.is_file():
        raise FileNotFoundError(db)

    with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as conn:
        docs = source_documents(conn)

    missing = []
    bad_pit = []
    for d in docs:
        if not resolve_storage(d["storage_path"]).is_file():
            missing.append(d["corpus_document_id"])
        if int(d["availability_is_point_in_time"] or 0) != 0:
            bad_pit.append(d["corpus_document_id"])

    failures = []
    if not docs:
        failures.append("zero_ex21_documents")
    if missing:
        failures.append("missing_raw_storage")
    if bad_pit:
        failures.append("historical_ex21_incorrectly_marked_pit")

    return {
        "status": "FAIL" if failures else "PASS",
        "failures": failures,
        "selected_documents": len(docs),
        "missing_storage_count": len(missing),
        "nonzero_pit_documents": len(bad_pit),
        "strict_historical_pit": False,
        "main_db_mutated": False,
        "canonical_entities_created": False,
        "graph_edges_written": False,
        "next_gate": (
            "If PASS, build structured EX-21 evidence rows in a separate DB."
        ),
    }


def ensure_schema(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE extraction_runs(
      extraction_run_id TEXT PRIMARY KEY,
      version TEXT NOT NULL,
      status TEXT NOT NULL,
      config_json TEXT NOT NULL,
      config_sha256 TEXT NOT NULL,
      started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      finished_at TEXT,
      documents_scanned INTEGER NOT NULL DEFAULT 0,
      documents_with_rows INTEGER NOT NULL DEFAULT 0,
      rows_written INTEGER NOT NULL DEFAULT 0,
      error_json TEXT
    );

    CREATE TABLE structured_documents(
      corpus_document_id TEXT PRIMARY KEY,
      extraction_run_id TEXT NOT NULL,
      accession_number TEXT NOT NULL,
      registrant_asset_id INTEGER NOT NULL,
      registrant_entity_id INTEGER NOT NULL,
      registrant_ticker TEXT,
      evidence_available_at TEXT NOT NULL,
      availability_is_point_in_time INTEGER NOT NULL DEFAULT 0,
      raw_sha256 TEXT NOT NULL,
      source_url TEXT,
      html_table_count INTEGER NOT NULL,
      data_table_count INTEGER NOT NULL,
      explicit_schema_tables INTEGER NOT NULL,
      implicit_schema_tables INTEGER NOT NULL,
      inherited_schema_tables INTEGER NOT NULL,
      footnote_tables_skipped INTEGER NOT NULL,
      uniform_format_span_tables INTEGER NOT NULL,
      unsupported_tables INTEGER NOT NULL,
      metadata_json TEXT
    );

    CREATE TABLE structured_ex21_rows(
      structured_row_id TEXT PRIMARY KEY,
      extraction_run_id TEXT NOT NULL,
      corpus_document_id TEXT NOT NULL,
      accession_number TEXT NOT NULL,
      registrant_asset_id INTEGER NOT NULL,
      registrant_entity_id INTEGER NOT NULL,
      registrant_ticker TEXT,
      evidence_available_at TEXT NOT NULL,
      availability_is_point_in_time INTEGER NOT NULL DEFAULT 0,
      table_index INTEGER NOT NULL,
      source_row_index INTEGER NOT NULL,
      schema_family TEXT NOT NULL,
      schema_json TEXT NOT NULL,
      legal_name_raw TEXT NOT NULL,
      legal_name_clean TEXT NOT NULL,
      legal_name_footnote_refs_json TEXT NOT NULL,
      jurisdiction_raw TEXT,
      location_raw TEXT,
      dba_alias_raw TEXT,
      ownership_raw TEXT,
      ownership_percent REAL,
      raw_cells_json TEXT NOT NULL,
      raw_sha256 TEXT NOT NULL,
      source_url TEXT,
      metadata_json TEXT,
      UNIQUE(
        extraction_run_id,corpus_document_id,table_index,source_row_index
      )
    );

    CREATE INDEX idx_ex21_name
      ON structured_ex21_rows(legal_name_clean);
    CREATE INDEX idx_ex21_jurisdiction
      ON structured_ex21_rows(jurisdiction_raw);
    CREATE INDEX idx_ex21_registrant_time
      ON structured_ex21_rows(
        registrant_entity_id,evidence_available_at
      );

    CREATE TABLE qa_samples(
      qa_sample_id TEXT PRIMARY KEY,
      structured_row_id TEXT NOT NULL,
      sample_group TEXT NOT NULL,
      deterministic_rank TEXT NOT NULL,
      manual_label TEXT,
      manual_notes TEXT,
      UNIQUE(structured_row_id,sample_group)
    );
    """)


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join(str(x) for x in parts).encode("utf-8")
    return prefix + "_" + hashlib.sha256(raw).hexdigest()


def extract_document(
    d: dict[str, Any],
    cfg: dict,
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    path = resolve_storage(d["storage_path"])
    storage = path.read_bytes()
    payload = decode_payload(storage, d["content_encoding"])
    actual_sha = hashlib.sha256(payload).hexdigest()
    if actual_sha != str(d["raw_sha256"]):
        raise RuntimeError(
            f"raw sha mismatch for {d['corpus_document_id']}"
        )

    parser = TableParser()
    parser.feed(payload.decode("utf-8", errors="replace"))

    inherited: dict[int, Schema] = {}
    out_rows = []
    counts = Counter()

    for table_idx, table in enumerate(parser.tables):
        if is_footnote_table(table):
            counts["footnote"] += 1
            continue
        if uniform_formatting_span(table):
            counts["uniform_span"] += 1

        schema = infer_schema(table, cfg, inherited)
        if schema is None:
            counts["unsupported"] += 1
            continue

        if schema.family == "explicit_header":
            counts["explicit"] += 1
        elif schema.family == "implicit_legal_name":
            counts["implicit"] += 1
        else:
            counts["inherited"] += 1

        inherited[schema.arity] = schema
        counts["data"] += 1

        start_idx = (
            schema.header_row_index + 1
            if schema.header_row_index >= 0
            else 0
        )
        for source_row_idx, row in enumerate(table[start_idx:], start=start_idx):
            if len(row) != schema.arity:
                continue

            name_col = schema.role_to_col["legal_name"]
            name_raw = clean_text(row[name_col].text)
            if not name_raw:
                continue

            # Repeated headers inside long tables are skipped.
            if role_for_header(name_raw, cfg):
                continue

            # Narrative rows are not entity rows.
            if len(name_raw) > 220:
                continue
            if re.fullmatch(r"\(?\d+\)?", name_raw):
                continue

            name_clean, footnotes = split_trailing_footnotes(name_raw)
            if not name_clean:
                continue

            def value(role: str) -> str | None:
                col = schema.role_to_col.get(role)
                if col is None or col >= len(row):
                    return None
                v = clean_text(row[col].text)
                return v or None

            ownership_raw = value("ownership")
            row_id = stable_id(
                "ex21row",
                d["corpus_document_id"],
                table_idx,
                source_row_idx,
            )
            out_rows.append({
                "structured_row_id": row_id,
                "table_index": table_idx,
                "source_row_index": source_row_idx,
                "schema_family": schema.family,
                "schema_json": json.dumps({
                    "arity": schema.arity,
                    "role_to_col": schema.role_to_col,
                    "inferred_roles": list(schema.inferred_roles),
                }, sort_keys=True),
                "legal_name_raw": name_raw,
                "legal_name_clean": name_clean,
                "legal_name_footnote_refs_json": json.dumps(footnotes),
                "jurisdiction_raw": value("jurisdiction"),
                "location_raw": value("location"),
                "dba_alias_raw": value("dba_alias"),
                "ownership_raw": ownership_raw,
                "ownership_percent": (
                    parse_ownership(ownership_raw)
                    if ownership_raw is not None
                    else None
                ),
                "raw_cells_json": json.dumps(
                    [c.text for c in row],
                    ensure_ascii=False,
                ),
            })

    summary = {
        "html_table_count": len(parser.tables),
        "data_table_count": counts["data"],
        "explicit_schema_tables": counts["explicit"],
        "implicit_schema_tables": counts["implicit"],
        "inherited_schema_tables": counts["inherited"],
        "footnote_tables_skipped": counts["footnote"],
        "uniform_format_span_tables": counts["uniform_span"],
        "unsupported_tables": counts["unsupported"],
    }
    return summary, out_rows


def build(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = load_config(config_path)
    pre = plan(config_path)
    if pre["status"] != "PASS":
        raise RuntimeError(pre["failures"])

    src_db = ROOT / cfg["corpus_db"]
    out_db = ROOT / cfg["output_db"]
    cfg_json = json.dumps(cfg, sort_keys=True)
    cfg_sha = hashlib.sha256(cfg_json.encode("utf-8")).hexdigest()
    run_id = stable_id("ex21structrun", cfg["version"], cfg_sha)

    with sqlite3.connect(
        f"file:{src_db.resolve()}?mode=ro", uri=True
    ) as src:
        docs = source_documents(src)

    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()

    result = {
        "status": "PASS",
        "extraction_run_id": run_id,
        "documents_scanned": 0,
        "documents_with_rows": 0,
        "rows_written": 0,
        "by_schema_family": Counter(),
        "tickers_with_rows": set(),
        "documents_without_rows": [],
        "strict_historical_pit": False,
        "main_db_mutated": False,
        "canonical_entities_created": False,
        "graph_edges_written": False,
    }

    with sqlite3.connect(out_db) as out:
        ensure_schema(out)
        out.execute(
            """
            INSERT INTO extraction_runs(
              extraction_run_id,version,status,config_json,config_sha256
            ) VALUES (?,?,?,?,?)
            """,
            (run_id, cfg["version"], "running", cfg_json, cfg_sha),
        )
        out.commit()

        try:
            for d in docs:
                summary, rows = extract_document(d, cfg)
                result["documents_scanned"] += 1
                if rows:
                    result["documents_with_rows"] += 1
                    result["tickers_with_rows"].add(str(d["asset_ticker"]))
                else:
                    result["documents_without_rows"].append(
                        d["corpus_document_id"]
                    )

                out.execute(
                    """
                    INSERT INTO structured_documents(
                      corpus_document_id,extraction_run_id,accession_number,
                      registrant_asset_id,registrant_entity_id,
                      registrant_ticker,evidence_available_at,
                      availability_is_point_in_time,raw_sha256,source_url,
                      html_table_count,data_table_count,
                      explicit_schema_tables,implicit_schema_tables,
                      inherited_schema_tables,footnote_tables_skipped,
                      uniform_format_span_tables,unsupported_tables,
                      metadata_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        d["corpus_document_id"],run_id,d["accession_number"],
                        int(d["asset_id"]),int(d["subject_entity_id"]),
                        d["asset_ticker"],d["effective_available_at"],0,
                        d["raw_sha256"],d["source_url"],
                        summary["html_table_count"],
                        summary["data_table_count"],
                        summary["explicit_schema_tables"],
                        summary["implicit_schema_tables"],
                        summary["inherited_schema_tables"],
                        summary["footnote_tables_skipped"],
                        summary["uniform_format_span_tables"],
                        summary["unsupported_tables"],
                        json.dumps({
                            "raw_sha256_verified": True,
                            "identity_decision": False,
                            "uniform_colspan_expanded": False,
                        }, sort_keys=True),
                    ),
                )

                for r in rows:
                    out.execute(
                        """
                        INSERT INTO structured_ex21_rows(
                          structured_row_id,extraction_run_id,
                          corpus_document_id,accession_number,
                          registrant_asset_id,registrant_entity_id,
                          registrant_ticker,evidence_available_at,
                          availability_is_point_in_time,
                          table_index,source_row_index,schema_family,
                          schema_json,legal_name_raw,legal_name_clean,
                          legal_name_footnote_refs_json,
                          jurisdiction_raw,location_raw,dba_alias_raw,
                          ownership_raw,ownership_percent,raw_cells_json,
                          raw_sha256,source_url,metadata_json
                        ) VALUES (
                          ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                        )
                        """,
                        (
                            r["structured_row_id"],run_id,
                            d["corpus_document_id"],d["accession_number"],
                            int(d["asset_id"]),int(d["subject_entity_id"]),
                            d["asset_ticker"],d["effective_available_at"],0,
                            r["table_index"],r["source_row_index"],
                            r["schema_family"],r["schema_json"],
                            r["legal_name_raw"],r["legal_name_clean"],
                            r["legal_name_footnote_refs_json"],
                            r["jurisdiction_raw"],r["location_raw"],
                            r["dba_alias_raw"],r["ownership_raw"],
                            r["ownership_percent"],r["raw_cells_json"],
                            d["raw_sha256"],d["source_url"],
                            json.dumps({
                                "canonical_entity_created": False,
                                "relation_promoted": False,
                            }, sort_keys=True),
                        ),
                    )
                    result["rows_written"] += 1
                    result["by_schema_family"][r["schema_family"]] += 1

            out.execute(
                """
                UPDATE extraction_runs
                SET status='completed',finished_at=CURRENT_TIMESTAMP,
                    documents_scanned=?,documents_with_rows=?,rows_written=?
                WHERE extraction_run_id=?
                """,
                (
                    result["documents_scanned"],
                    result["documents_with_rows"],
                    result["rows_written"],
                    run_id,
                ),
            )
            out.commit()
        except Exception:
            out.rollback()
            raise

    result["by_schema_family"] = dict(
        sorted(result["by_schema_family"].items())
    )
    result["tickers_with_rows"] = sorted(result["tickers_with_rows"])
    return result


def qa_sample(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = load_config(config_path)
    db = ROOT / cfg["output_db"]
    per_ticker = int(cfg["qa"]["sample_rows_per_ticker"])
    per_family = int(cfg["qa"]["sample_rows_per_schema_family"])

    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM qa_samples")
        rows = conn.execute(
            """
            SELECT structured_row_id,registrant_ticker,schema_family
            FROM structured_ex21_rows
            ORDER BY structured_row_id
            """
        ).fetchall()
        groups = defaultdict(list)
        for row_id, ticker, family in rows:
            groups[f"ticker:{ticker}"].append(str(row_id))
            groups[f"family:{family}"].append(str(row_id))

        for group, ids in sorted(groups.items()):
            limit = (
                per_ticker if group.startswith("ticker:")
                else per_family
            )
            ranked = sorted(
                ids,
                key=lambda x: hashlib.sha256(
                    f"{group}\0{x}".encode("utf-8")
                ).hexdigest(),
            )[:limit]
            for rank, row_id in enumerate(ranked):
                conn.execute(
                    """
                    INSERT INTO qa_samples(
                      qa_sample_id,structured_row_id,
                      sample_group,deterministic_rank
                    ) VALUES (?,?,?,?)
                    """,
                    (
                        stable_id("ex21qa",group,row_id),
                        row_id,group,f"{rank:06d}",
                    ),
                )
        conn.commit()

        sample = conn.execute(
            """
            SELECT
              q.sample_group,q.deterministic_rank,
              r.structured_row_id,r.registrant_ticker,
              r.accession_number,r.table_index,r.source_row_index,
              r.schema_family,r.legal_name_raw,r.legal_name_clean,
              r.legal_name_footnote_refs_json,
              r.jurisdiction_raw,r.location_raw,r.dba_alias_raw,
              r.ownership_raw,r.ownership_percent,
              r.raw_cells_json,r.source_url
            FROM qa_samples q
            JOIN structured_ex21_rows r
              ON r.structured_row_id=q.structured_row_id
            ORDER BY q.sample_group,q.deterministic_rank
            """
        ).fetchall()

    names = [
        "sample_group","deterministic_rank","structured_row_id",
        "registrant_ticker","accession_number","table_index",
        "source_row_index","schema_family","legal_name_raw",
        "legal_name_clean","legal_name_footnote_refs_json",
        "jurisdiction_raw","location_raw","dba_alias_raw",
        "ownership_raw","ownership_percent","raw_cells_json","source_url",
    ]
    payload = [dict(zip(names, row)) for row in sample]

    report_dir = ROOT / cfg["report_dir"]
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "qa_sample.json"
    path.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    return {
        "status": "PASS",
        "sample_rows": len(payload),
        "sample_file": str(path),
        "manual_review_required": True,
        "canonical_entity_creation_allowed": False,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument(
        "--stage",
        required=True,
        choices=("plan", "build", "qa-sample"),
    )
    a = p.parse_args()
    if a.stage == "plan":
        result = plan(a.config)
    elif a.stage == "build":
        result = build(a.config)
    else:
        result = qa_sample(a.config)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

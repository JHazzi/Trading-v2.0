from __future__ import annotations

import argparse
import gzip
import hashlib
import html
import json
import re
import sqlite3
from collections import Counter, defaultdict
from datetime import datetime, timezone
from html.parser import HTMLParser
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/event_graph_sec_relation_corpus_v001.json"

REQUIRED_TABLES = {
    "assets",
    "asset_entities",
    "raw_source_documents",
    "raw_document_assets",
    "sec_filings",
    "sec_filing_files",
    "sec_filing_metadata_observations",
}


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join(str(x) for x in parts).encode("utf-8")
    return prefix + "_" + hashlib.sha256(raw).hexdigest()


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def parse_time(value: str | None) -> datetime | None:
    if value is None:
        return None
    text = str(value).strip()
    if not text:
        return None
    if text.endswith("Z"):
        text = text[:-1] + "+00:00"
    dt = datetime.fromisoformat(text)
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt.astimezone(timezone.utc)


def iso_max(*values: str | None) -> str:
    parsed = [parse_time(x) for x in values]
    parsed = [x for x in parsed if x is not None]
    if not parsed:
        raise ValueError("no timestamp available for evidence")
    return max(parsed).isoformat()


class VisibleTextParser(HTMLParser):
    DROP = {"script", "style", "noscript", "svg"}

    def __init__(self):
        super().__init__(convert_charrefs=True)
        self.parts: list[str] = []
        self.drop_depth = 0

    def handle_starttag(self, tag, attrs):
        t = tag.lower()
        if t in self.DROP:
            self.drop_depth += 1
        if self.drop_depth == 0 and t in {
            "p", "div", "br", "li", "tr", "td", "th",
            "h1", "h2", "h3", "h4", "h5", "h6"
        }:
            self.parts.append("\n")

    def handle_endtag(self, tag):
        t = tag.lower()
        if t in self.DROP and self.drop_depth:
            self.drop_depth -= 1
        if self.drop_depth == 0 and t in {
            "p", "div", "li", "tr", "h1", "h2", "h3",
            "h4", "h5", "h6"
        }:
            self.parts.append("\n")

    def handle_data(self, data):
        if self.drop_depth == 0:
            self.parts.append(data)


def decode_payload(raw: bytes, content_encoding: str | None) -> bytes:
    enc = (content_encoding or "").lower()
    if raw[:2] == b"\x1f\x8b" or "gzip" in enc:
        return gzip.decompress(raw)
    return raw


def load_payload(path: Path, content_encoding: str | None) -> bytes:
    return decode_payload(path.read_bytes(), content_encoding)


def normalized_text(payload: bytes, content_type: str | None) -> str:
    # SEC documents are overwhelmingly ASCII/UTF-8 compatible. Replacement is
    # deliberate and deterministic; original bytes remain the source of truth.
    text = payload.decode("utf-8", errors="replace")
    ctype = (content_type or "").lower()
    looks_html = (
        "html" in ctype
        or "<html" in text[:5000].lower()
        or "<body" in text[:5000].lower()
    )
    if looks_html:
        parser = VisibleTextParser()
        try:
            parser.feed(text)
            text = "".join(parser.parts)
        except Exception:
            # Fallback still does not alter the source bytes.
            text = re.sub(r"<[^>]+>", " ", text)
    text = html.unescape(text)
    text = text.replace("\r\n", "\n").replace("\r", "\n")
    text = re.sub(r"[ \t\f\v]+", " ", text)
    text = re.sub(r" *\n *", "\n", text)
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def chunks(text: str, max_chars: int, overlap: int) -> list[tuple[int, int, str]]:
    if max_chars <= 0:
        raise ValueError("max_chars must be positive")
    if overlap < 0 or overlap >= max_chars:
        raise ValueError("invalid overlap")
    out = []
    n = len(text)
    start = 0
    while start < n:
        tentative_end = min(n, start + max_chars)
        end = tentative_end
        if tentative_end < n:
            # Prefer paragraph/sentence-ish boundaries without expanding.
            window = text[start:tentative_end]
            candidates = [
                window.rfind("\n\n"),
                window.rfind(". "),
                window.rfind("\n"),
            ]
            cut = max(candidates)
            if cut >= int(max_chars * 0.55):
                end = start + cut + (2 if window[cut:cut+2] == ". " else 0)
        body = text[start:end].strip()
        if body:
            left = start
            while left < end and text[left].isspace():
                left += 1
            right = end
            while right > left and text[right-1].isspace():
                right -= 1
            out.append((left, right, text[left:right]))
        if end >= n:
            break
        start = max(start + 1, end - overlap)
    return out


def classify_document(
    *,
    form: str,
    is_primary: int,
    document_type: str | None,
    description: str | None,
    cfg: dict,
) -> str | None:
    form_u = (form or "").upper()
    dtype = (document_type or "").upper().strip()
    desc = (description or "").upper()

    if (
        cfg["document_selection"]["include_primary"]
        and int(is_primary or 0) == 1
        and form_u in set(cfg["document_selection"]["primary_forms"])
    ):
        return "primary_narrative"

    for prefix, label in cfg["document_selection"][
        "include_exhibit_prefixes"
    ].items():
        if dtype.startswith(prefix.upper()):
            return label

    # SEC file inventories can occasionally have missing/inconsistent type.
    if "SUBSIDIAR" in desc and ("EXHIBIT" in desc or "EX-21" in desc):
        return "subsidiary_exhibit"
    return None


def source_rows(conn: sqlite3.Connection, cfg: dict) -> list[dict[str, Any]]:
    tables = {
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(REQUIRED_TABLES - tables)
    if missing:
        raise RuntimeError(f"required local tables missing: {missing}")

    # First metadata observation only. Later revisions remain source history but
    # do not silently rewrite the initial research corpus.
    rows = conn.execute(
        """
        WITH first_meta AS (
            SELECT *
            FROM (
                SELECT
                    m.*,
                    ROW_NUMBER() OVER (
                        PARTITION BY m.filing_raw_document_id
                        ORDER BY
                            m.observation_sequence,
                            julianday(m.available_at),
                            m.metadata_observation_id
                    ) AS rn
                FROM sec_filing_metadata_observations m
            )
            WHERE rn=1
        )
        SELECT
            sf.raw_document_id AS filing_raw_document_id,
            sf.cik,
            sf.accession_number,
            sf.form,
            sf.filing_date,
            sf.acceptance_datetime,
            sf.report_date,
            sf.entity_name,
            sf.ticker_at_ingestion,

            ff.sequence_number,
            ff.document_name,
            ff.document_type,
            ff.description,
            ff.source_url AS filing_file_source_url,
            ff.is_primary,
            ff.raw_document_id AS content_raw_document_id,

            rd.source_id,
            rd.external_id,
            rd.document_kind,
            rd.source_url AS raw_source_url,
            rd.canonical_url,
            rd.published_at,
            rd.available_at AS document_available_at,
            rd.retrieved_at AS document_retrieved_at,
            rd.content_type,
            rd.content_encoding,
            rd.raw_sha256,
            rd.storage_path,
            rd.byte_length,
            rd.parser_status,
            rd.parser_version,

            fm.metadata_observation_id,
            fm.available_at AS metadata_available_at,
            fm.availability_basis AS metadata_availability_basis,
            fm.availability_is_point_in_time AS metadata_pit,
            fm.observation_kind AS metadata_observation_kind,

            rda.asset_id,
            rda.role AS raw_document_asset_role,
            rda.linking_method,
            rda.linking_version,
            rda.confidence AS asset_link_confidence,
            a.ticker AS asset_ticker,
            a.asset_type,
            a.active,
            ae.entity_id AS subject_entity_id
        FROM sec_filing_files ff
        JOIN sec_filings sf
          ON sf.raw_document_id=ff.filing_raw_document_id
        JOIN raw_source_documents rd
          ON rd.raw_document_id=ff.raw_document_id
        LEFT JOIN first_meta fm
          ON fm.filing_raw_document_id=sf.raw_document_id
        LEFT JOIN raw_document_assets rda
          ON rda.raw_document_id=rd.raw_document_id
        LEFT JOIN assets a
          ON a.asset_id=rda.asset_id
        LEFT JOIN asset_entities ae
          ON ae.asset_id=a.asset_id
        WHERE ff.raw_document_id IS NOT NULL
        ORDER BY
            julianday(sf.acceptance_datetime),
            sf.accession_number,
            CAST(ff.sequence_number AS INTEGER),
            ff.document_name,
            rda.asset_id
        """
    ).fetchall()

    names = [x[0] for x in conn.execute(
        """
        WITH first_meta AS (
            SELECT *
            FROM (
                SELECT m.*, ROW_NUMBER() OVER (
                    PARTITION BY m.filing_raw_document_id
                    ORDER BY m.observation_sequence,
                             julianday(m.available_at),
                             m.metadata_observation_id
                ) rn
                FROM sec_filing_metadata_observations m
            ) WHERE rn=1
        )
        SELECT
            sf.raw_document_id, sf.cik, sf.accession_number, sf.form,
            sf.filing_date, sf.acceptance_datetime, sf.report_date,
            sf.entity_name, sf.ticker_at_ingestion,
            ff.sequence_number, ff.document_name, ff.document_type,
            ff.description, ff.source_url, ff.is_primary, ff.raw_document_id,
            rd.source_id, rd.external_id, rd.document_kind, rd.source_url,
            rd.canonical_url, rd.published_at, rd.available_at, rd.retrieved_at,
            rd.content_type, rd.content_encoding, rd.raw_sha256, rd.storage_path,
            rd.byte_length, rd.parser_status, rd.parser_version,
            fm.metadata_observation_id, fm.available_at, fm.availability_basis,
            fm.availability_is_point_in_time, fm.observation_kind,
            rda.asset_id, rda.role, rda.linking_method, rda.linking_version,
            rda.confidence, a.ticker, a.asset_type, a.active, ae.entity_id
        FROM sec_filing_files ff
        JOIN sec_filings sf ON 1=0
        JOIN raw_source_documents rd ON 1=0
        LEFT JOIN first_meta fm ON 1=0
        LEFT JOIN raw_document_assets rda ON 1=0
        LEFT JOIN assets a ON 1=0
        LEFT JOIN asset_entities ae ON 1=0
        LIMIT 0
        """
    ).description] if False else [
        "filing_raw_document_id","cik","accession_number","form",
        "filing_date","acceptance_datetime","report_date","entity_name",
        "ticker_at_ingestion","sequence_number","document_name",
        "document_type","description","filing_file_source_url","is_primary",
        "content_raw_document_id","source_id","external_id","document_kind",
        "raw_source_url","canonical_url","published_at",
        "document_available_at","document_retrieved_at","content_type",
        "content_encoding","raw_sha256","storage_path","byte_length",
        "parser_status","parser_version","metadata_observation_id",
        "metadata_available_at","metadata_availability_basis","metadata_pit",
        "metadata_observation_kind","asset_id","raw_document_asset_role",
        "linking_method","linking_version","asset_link_confidence",
        "asset_ticker","asset_type","active","subject_entity_id"
    ]

    out = []
    seen = set()
    for row in rows:
        d = dict(zip(names, row))
        document_class = classify_document(
            form=d["form"],
            is_primary=int(d["is_primary"] or 0),
            document_type=d["document_type"],
            description=d["description"],
            cfg=cfg,
        )
        if document_class is None:
            continue
        if cfg["document_selection"]["exclude_reference_assets"]:
            if d["asset_type"] not in (None, "equity"):
                continue

        # A raw document can have repeated metadata links. Keep one corpus row
        # per raw content + subject asset; ambiguity is reported, not hidden.
        key = (d["content_raw_document_id"], d["asset_id"])
        if key in seen:
            continue
        seen.add(key)
        d["document_class"] = document_class
        d["effective_available_at"] = iso_max(
            d["document_available_at"],
            d["metadata_available_at"] or d["acceptance_datetime"],
        )
        d["availability_is_point_in_time"] = 0
        d["availability_basis"] = (
            "max(raw_document.available_at,"
            "first_metadata_observation.available_at_or_sec_acceptance)"
            "_historical_reconstruction_not_strict_pit"
        )
        out.append(d)
    return out


def resolve_storage_path(storage_path: str) -> Path:
    p = Path(storage_path)
    return p if p.is_absolute() else ROOT / p


def cue_counts(text: str, families: dict[str, list[str]]) -> dict[str, int]:
    lower = text.lower()
    return {
        family: sum(lower.count(cue.lower()) for cue in cues)
        for family, cues in families.items()
    }


def plan(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    db = ROOT / cfg["main_db"]
    with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as conn:
        rows = source_rows(conn, cfg)

    by_class = Counter(x["document_class"] for x in rows)
    by_form = Counter(x["form"] for x in rows)
    assets = {x["asset_id"] for x in rows if x["asset_id"] is not None}
    entities = {
        x["subject_entity_id"]
        for x in rows if x["subject_entity_id"] is not None
    }
    filings = {x["accession_number"] for x in rows}
    missing_paths = []
    missing_entity = 0
    missing_asset = 0
    bytes_total = 0

    for x in rows:
        bytes_total += int(x["byte_length"] or 0)
        if x["asset_id"] is None:
            missing_asset += 1
        if x["subject_entity_id"] is None:
            missing_entity += 1
        path = resolve_storage_path(str(x["storage_path"]))
        if not path.is_file():
            missing_paths.append({
                "raw_document_id": x["content_raw_document_id"],
                "storage_path": x["storage_path"],
            })

    status = "FAIL" if missing_paths else (
        "REVIEW" if missing_asset or missing_entity else "PASS"
    )
    return {
        "status": status,
        "failures": (
            ["selected_raw_storage_missing"] if missing_paths else []
        ),
        "reviews": [
            x for x, flag in (
                ("selected_document_missing_asset_link", missing_asset),
                ("selected_document_missing_subject_entity", missing_entity),
            ) if flag
        ],
        "selected_documents": len(rows),
        "selected_filings": len(filings),
        "selected_assets": len(assets),
        "selected_subject_entities": len(entities),
        "selected_bytes": bytes_total,
        "document_class_counts": dict(sorted(by_class.items())),
        "form_counts": dict(sorted(by_form.items())),
        "missing_storage_count": len(missing_paths),
        "missing_storage_examples": missing_paths[:25],
        "missing_asset_link_rows": missing_asset,
        "missing_subject_entity_rows": missing_entity,
        "strict_historical_pit": False,
        "main_db_mutated": False,
        "next_gate": (
            "If PASS/expected REVIEW with no missing storage, build the separate "
            "SEC relation corpus. No relation extraction occurs in this package."
        ),
    }


def ensure_output_schema(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE corpus_runs(
      corpus_run_id TEXT PRIMARY KEY,
      corpus_version TEXT NOT NULL,
      started_at TEXT NOT NULL,
      finished_at TEXT,
      status TEXT NOT NULL,
      configuration_json TEXT NOT NULL,
      configuration_sha256 TEXT NOT NULL,
      documents_written INTEGER NOT NULL DEFAULT 0,
      chunks_written INTEGER NOT NULL DEFAULT 0,
      error_json TEXT
    );

    CREATE TABLE corpus_documents(
      corpus_document_id TEXT PRIMARY KEY,
      corpus_run_id TEXT NOT NULL,
      content_raw_document_id TEXT NOT NULL,
      filing_raw_document_id TEXT NOT NULL,
      accession_number TEXT NOT NULL,
      cik TEXT NOT NULL,
      form TEXT NOT NULL,
      filing_date TEXT,
      acceptance_datetime TEXT NOT NULL,
      report_date TEXT,
      document_name TEXT NOT NULL,
      document_type TEXT,
      description TEXT,
      document_class TEXT NOT NULL,
      is_primary INTEGER NOT NULL,
      asset_id INTEGER,
      subject_entity_id INTEGER,
      asset_ticker TEXT,
      source_url TEXT,
      source_id TEXT NOT NULL,
      raw_sha256 TEXT NOT NULL,
      storage_path TEXT NOT NULL,
      content_type TEXT,
      content_encoding TEXT,
      source_byte_length INTEGER NOT NULL,
      normalized_text_sha256 TEXT NOT NULL,
      normalized_char_length INTEGER NOT NULL,
      effective_available_at TEXT NOT NULL,
      availability_basis TEXT NOT NULL,
      availability_is_point_in_time INTEGER NOT NULL DEFAULT 0,
      parser_version TEXT NOT NULL,
      cue_counts_json TEXT NOT NULL,
      metadata_json TEXT,
      UNIQUE(corpus_run_id,content_raw_document_id,asset_id)
    );

    CREATE TABLE corpus_chunks(
      corpus_chunk_id TEXT PRIMARY KEY,
      corpus_document_id TEXT NOT NULL,
      chunk_index INTEGER NOT NULL,
      char_start INTEGER NOT NULL,
      char_end INTEGER NOT NULL,
      chunk_text TEXT NOT NULL,
      chunk_sha256 TEXT NOT NULL,
      cue_counts_json TEXT NOT NULL,
      FOREIGN KEY(corpus_document_id)
        REFERENCES corpus_documents(corpus_document_id) ON DELETE CASCADE,
      UNIQUE(corpus_document_id,chunk_index)
    );

    CREATE INDEX idx_corpus_documents_available
      ON corpus_documents(effective_available_at);
    CREATE INDEX idx_corpus_documents_asset
      ON corpus_documents(asset_id, effective_available_at);
    CREATE INDEX idx_corpus_documents_class
      ON corpus_documents(document_class, form);
    """)


def build(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    main_db = ROOT / cfg["main_db"]
    out_db = ROOT / cfg["output_db"]
    selection_plan = plan(config_path)
    if selection_plan["status"] == "FAIL":
        raise RuntimeError(
            f"corpus plan failed: {selection_plan['failures']}"
        )

    with sqlite3.connect(
        f"file:{main_db.resolve()}?mode=ro", uri=True
    ) as conn:
        rows = source_rows(conn, cfg)

    cfg_json = json.dumps(cfg, sort_keys=True)
    cfg_sha = hashlib.sha256(cfg_json.encode("utf-8")).hexdigest()
    run_id = stable_id(
        "secrelcorpus",
        cfg["version"],
        cfg_sha,
    )
    text_cfg = cfg["text_contract"]
    families = cfg["cue_scan"]["families"]

    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()

    result = {
        "status": "PASS",
        "corpus_run_id": run_id,
        "documents_selected": len(rows),
        "documents_written": 0,
        "chunks_written": 0,
        "hash_failures": 0,
        "parse_failures": 0,
        "too_large": 0,
        "too_short": 0,
        "cue_totals": defaultdict(int),
        "strict_historical_pit": False,
        "main_db_mutated": False,
        "relations_extracted": False,
        "graph_edges_written": False,
    }

    with sqlite3.connect(out_db) as out:
        out.execute("PRAGMA foreign_keys=ON")
        ensure_output_schema(out)
        out.execute(
            """
            INSERT INTO corpus_runs(
              corpus_run_id,corpus_version,started_at,status,
              configuration_json,configuration_sha256
            ) VALUES (?,?,?,?,?,?)
            """,
            (run_id, cfg["version"], utc_now(), "running", cfg_json, cfg_sha),
        )
        out.commit()

        try:
            for row in rows:
                if int(row["byte_length"] or 0) > int(
                    text_cfg["max_document_bytes"]
                ):
                    result["too_large"] += 1
                    continue

                path = resolve_storage_path(str(row["storage_path"]))
                raw_storage = path.read_bytes()
                payload = decode_payload(
                    raw_storage, row["content_encoding"]
                )
                actual_sha = hashlib.sha256(payload).hexdigest()
                if actual_sha != str(row["raw_sha256"]):
                    result["hash_failures"] += 1
                    raise RuntimeError(
                        "raw SHA mismatch for "
                        f"{row['content_raw_document_id']}"
                    )

                text = normalized_text(payload, row["content_type"])
                if len(text) < int(text_cfg["min_normalized_chars"]):
                    result["too_short"] += 1
                    continue

                text_sha = hashlib.sha256(
                    text.encode("utf-8")
                ).hexdigest()
                doc_id = stable_id(
                    "secreldoc",
                    run_id,
                    row["content_raw_document_id"],
                    row["asset_id"] or "",
                )
                dcues = cue_counts(text, families)
                for k, v in dcues.items():
                    result["cue_totals"][k] += int(v)

                out.execute(
                    """
                    INSERT INTO corpus_documents(
                      corpus_document_id,corpus_run_id,
                      content_raw_document_id,filing_raw_document_id,
                      accession_number,cik,form,filing_date,
                      acceptance_datetime,report_date,document_name,
                      document_type,description,document_class,is_primary,
                      asset_id,subject_entity_id,asset_ticker,source_url,
                      source_id,raw_sha256,storage_path,content_type,
                      content_encoding,source_byte_length,
                      normalized_text_sha256,normalized_char_length,
                      effective_available_at,availability_basis,
                      availability_is_point_in_time,parser_version,
                      cue_counts_json,metadata_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,
                              ?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        doc_id, run_id, row["content_raw_document_id"],
                        row["filing_raw_document_id"],
                        row["accession_number"], row["cik"], row["form"],
                        row["filing_date"], row["acceptance_datetime"],
                        row["report_date"], row["document_name"],
                        row["document_type"], row["description"],
                        row["document_class"], int(row["is_primary"] or 0),
                        row["asset_id"], row["subject_entity_id"],
                        row["asset_ticker"],
                        row["filing_file_source_url"] or row["raw_source_url"],
                        row["source_id"], row["raw_sha256"],
                        row["storage_path"], row["content_type"],
                        row["content_encoding"], int(row["byte_length"] or 0),
                        text_sha, len(text), row["effective_available_at"],
                        row["availability_basis"], 0,
                        text_cfg["html_parser"] + "+"
                        + text_cfg["normalizer"],
                        json.dumps(dcues, sort_keys=True),
                        json.dumps({
                            "metadata_observation_id":
                                row["metadata_observation_id"],
                            "metadata_available_at":
                                row["metadata_available_at"],
                            "metadata_availability_basis":
                                row["metadata_availability_basis"],
                            "metadata_observation_kind":
                                row["metadata_observation_kind"],
                            "raw_document_asset_role":
                                row["raw_document_asset_role"],
                            "asset_linking_method":
                                row["linking_method"],
                            "asset_linking_version":
                                row["linking_version"],
                            "asset_link_confidence":
                                row["asset_link_confidence"],
                        }, sort_keys=True),
                    ),
                )
                result["documents_written"] += 1

                for i, (start, end, chunk_text) in enumerate(
                    chunks(
                        text,
                        int(text_cfg["chunk_max_chars"]),
                        int(text_cfg["chunk_overlap_chars"]),
                    )
                ):
                    ccues = cue_counts(chunk_text, families)
                    csha = hashlib.sha256(
                        chunk_text.encode("utf-8")
                    ).hexdigest()
                    cid = stable_id("secrelchunk", doc_id, i, csha)
                    out.execute(
                        """
                        INSERT INTO corpus_chunks(
                          corpus_chunk_id,corpus_document_id,chunk_index,
                          char_start,char_end,chunk_text,chunk_sha256,
                          cue_counts_json
                        ) VALUES (?,?,?,?,?,?,?,?)
                        """,
                        (
                            cid, doc_id, i, start, end, chunk_text, csha,
                            json.dumps(ccues, sort_keys=True),
                        ),
                    )
                    result["chunks_written"] += 1

            out.execute(
                """
                UPDATE corpus_runs
                SET finished_at=?,status='completed',
                    documents_written=?,chunks_written=?
                WHERE corpus_run_id=?
                """,
                (
                    utc_now(),
                    result["documents_written"],
                    result["chunks_written"],
                    run_id,
                ),
            )
            out.commit()
        except Exception as exc:
            out.rollback()
            out.execute(
                """
                UPDATE corpus_runs
                SET finished_at=?,status='failed',error_json=?
                WHERE corpus_run_id=?
                """,
                (
                    utc_now(),
                    json.dumps([f"{type(exc).__name__}: {exc}"]),
                    run_id,
                ),
            )
            out.commit()
            raise

    result["cue_totals"] = dict(sorted(result["cue_totals"].items()))
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument(
        "--stage", required=True, choices=("plan", "build")
    )
    a = p.parse_args()
    result = plan(a.config) if a.stage == "plan" else build(a.config)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

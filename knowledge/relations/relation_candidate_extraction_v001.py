from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path
from typing import Iterable

from knowledge.entities.exact_entity_resolution_v001 import (
    ExactEntityResolver,
    normalize_entity_name,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = (
    ROOT / "config/event_graph_relation_candidates_v001.json"
)

LEGAL_SUFFIX = (
    r"(?:"
    r"Inc\.?|Incorporated|Corp\.?|Corporation|Company|Co\.?|"
    r"LLC|L\.L\.C\.|LP|L\.P\.|LLP|L\.L\.P\.|"
    r"Ltd\.?|Limited|PLC|plc|"
    r"GmbH|AG|SE|"
    r"S\.?A\.?|S\.?A\.?S\.?|S\.?p\.?A\.?|"
    r"Sarl|SARL|Sàrl|S\.?\s*à\s*r\.?\s*l\.?|"
    r"B\.?V\.?|N\.?V\.?|"
    r"Pty\.?\s+Ltd\.?|Pte\.?\s+Ltd\.?|"
    r"K\.?K\.?|Oy|AB|A/S|AS|Kft\.?|"
    r"Sp\.?\s*z\.?\s*o\.?\s*o\.?|"
    r"LLC|Ltda\.?|S\.?\s*de\s*R\.?L\.?"
    r")"
)

# Capitalized legal-name candidate ending in a legal form.
ORG_RE = re.compile(
    rf"""
    (?<![\w])
    (
      [A-Z0-9][A-Za-z0-9À-ÿ&'’.,()/_+\-]*
      (?:
        \s+
        (?:
          [A-Z0-9][A-Za-z0-9À-ÿ&'’.,()/_+\-]*
          |of|the|and|for|de|del|la|le|du|des|y|&|Holdings?|International
        )
      ){{0,16}}?
      \s+{LEGAL_SUFFIX}
    )
    (?=$|[\s,;:()\[\]"'])
    """,
    re.VERBOSE,
)

HEADER_OR_PROSE = re.compile(
    r"""
    (?:
      ^exhibit\b
      |subsidiar(?:y|ies)\s+of\b
      |list\s+of\s+(?:company\s+)?subsidiaries
      |name\s+of\s+subsidiar
      |place\s+of\s+(?:incorporation|formation)
      |state\s+or\s+jurisdiction
      |jurisdiction\s+of
      |in\s+accordance\s+with
      |certain\s+subsidiaries
      |the\s+company\s+has\s+omitted
      |regulation\s+s-k
      |rule\s+1-02
    )
    """,
    re.IGNORECASE | re.VERBOSE,
)

GENERIC_ENTITY = {
    "the company",
    "the corporation",
    "the borrower",
    "the lender",
    "the registrant",
    "the seller",
    "the buyer",
    "the parties",
}

PREAMBLE_RE = re.compile(
    r"\b(?:by\s+and\s+between|by\s+and\s+among|among\s+the\s+following)\b",
    re.IGNORECASE,
)


@dataclass(frozen=True)
class Candidate:
    relation_type: str
    target_name_raw: str
    evidence_start: int
    evidence_end: int
    evidence_text: str
    extraction_method: str
    extraction_rule: str


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join(str(x) for x in parts).encode("utf-8")
    return prefix + "_" + hashlib.sha256(raw).hexdigest()


def clean_name(value: str) -> str:
    text = re.sub(r"\s+", " ", value).strip()
    return text.strip(" \t\r\n,;:.()[]")


def reconstruct_document(
    conn: sqlite3.Connection,
    corpus_document_id: str,
    expected_length: int,
) -> str:
    rows = conn.execute(
        """
        SELECT char_start,char_end,chunk_text
        FROM corpus_chunks
        WHERE corpus_document_id=?
        ORDER BY chunk_index
        """,
        (corpus_document_id,),
    ).fetchall()
    if not rows:
        return ""

    pieces: list[str] = []
    cursor = 0
    for start, end, body in rows:
        start = int(start)
        end = int(end)
        body = str(body)
        if len(body) != end - start:
            raise RuntimeError(
                f"chunk offset mismatch for {corpus_document_id}"
            )
        if start > cursor:
            pieces.append(" " * (start - cursor))
            cursor = start
        if end <= cursor:
            continue
        skip = max(0, cursor - start)
        pieces.append(body[skip:])
        cursor = end

    if cursor < int(expected_length):
        pieces.append(" " * (int(expected_length) - cursor))
    text = "".join(pieces)
    if len(text) != int(expected_length):
        raise RuntimeError(
            f"reconstructed length mismatch {corpus_document_id}: "
            f"{len(text)} != {expected_length}"
        )
    return text


def extract_exhibit21(text: str) -> list[Candidate]:
    out: list[Candidate] = []
    seen = set()
    offset = 0

    for raw_line in text.splitlines(keepends=True):
        line_no_newline = raw_line.rstrip("\r\n")
        stripped = clean_name(line_no_newline)
        if (
            len(stripped) < 3
            or len(stripped) > 180
            or HEADER_OR_PROSE.search(stripped)
        ):
            offset += len(raw_line)
            continue

        # Capture only through the legal suffix. If a table was flattened as
        # "Entity Inc. Delaware", jurisdiction text is not included.
        match = ORG_RE.search(stripped)
        if match is None:
            offset += len(raw_line)
            continue

        name = clean_name(match.group(1))
        key = normalize_entity_name(name)
        if not key or key in GENERIC_ENTITY or key in seen:
            offset += len(raw_line)
            continue

        # Reject prose-like lines around the matched name.
        before = stripped[: match.start()].strip()
        if before and len(before.split()) > 3:
            offset += len(raw_line)
            continue

        local_start = line_no_newline.find(match.group(1))
        if local_start < 0:
            offset += len(raw_line)
            continue
        start = offset + local_start
        end = start + len(match.group(1))
        evidence_left = max(0, start - 80)
        evidence_right = min(len(text), end + 120)
        out.append(
            Candidate(
                relation_type="parent_of",
                target_name_raw=name,
                evidence_start=start,
                evidence_end=end,
                evidence_text=text[evidence_left:evidence_right].strip(),
                extraction_method="exhibit21_parent_of_v001",
                extraction_rule="legal_suffix_anchored_exhibit21_line",
            )
        )
        seen.add(key)
        offset += len(raw_line)

    return out


def extract_agreement_counterparties(
    text: str,
    *,
    prefix_chars: int = 24000,
) -> list[Candidate]:
    prefix = text[: int(prefix_chars)]
    out: list[Candidate] = []
    seen = set()

    for marker in PREAMBLE_RE.finditer(prefix):
        # Legal parties normally appear immediately after the explicit marker.
        # Restrict the window aggressively to preserve precision.
        window_start = marker.start()
        window_end = min(
            len(prefix),
            marker.end() + 2200,
        )
        window = prefix[marker.end():window_end]

        # Stop at recitals/operative text when identifiable.
        stop = re.search(
            r"""
            (?:
              \bWHEREAS\b
              |\bWITNESSETH\b
              |\bRECITALS?\b
              |\bNOW,\s+THEREFORE\b
            )
            """,
            window,
            re.IGNORECASE | re.VERBOSE,
        )
        if stop:
            window = window[: stop.start()]
            window_end = marker.end() + stop.start()

        for org in ORG_RE.finditer(window):
            raw_name = clean_name(org.group(1))
            norm = normalize_entity_name(raw_name)
            if not norm or norm in GENERIC_ENTITY or norm in seen:
                continue

            start = marker.end() + org.start(1)
            end = marker.end() + org.end(1)
            context_left = max(window_start, start - 280)
            context_right = min(len(prefix), end + 420)
            out.append(
                Candidate(
                    relation_type="contract_party_of",
                    target_name_raw=raw_name,
                    evidence_start=start,
                    evidence_end=end,
                    evidence_text=prefix[
                        context_left:context_right
                    ].strip(),
                    extraction_method="agreement_counterparty_v001",
                    extraction_rule="legal_entity_near_explicit_party_preamble",
                )
            )
            seen.add(norm)

        # One explicit preamble per document is enough for V001. Later repeated
        # operative clauses should not multiply the same candidate.
        if out:
            break

    return out


def ensure_schema(conn: sqlite3.Connection):
    conn.executescript(
        """
        CREATE TABLE extraction_runs(
          extraction_run_id TEXT PRIMARY KEY,
          extraction_version TEXT NOT NULL,
          started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
          finished_at TEXT,
          status TEXT NOT NULL,
          configuration_json TEXT NOT NULL,
          configuration_sha256 TEXT NOT NULL,
          documents_scanned INTEGER NOT NULL DEFAULT 0,
          candidates_written INTEGER NOT NULL DEFAULT 0,
          error_json TEXT
        );

        CREATE TABLE relation_name_candidates(
          relation_candidate_id TEXT PRIMARY KEY,
          extraction_run_id TEXT NOT NULL,
          corpus_document_id TEXT NOT NULL,
          content_raw_document_id TEXT NOT NULL,
          accession_number TEXT NOT NULL,
          document_class TEXT NOT NULL,
          source_asset_id INTEGER NOT NULL,
          source_entity_id INTEGER NOT NULL,
          source_ticker TEXT,
          relation_type TEXT NOT NULL,
          target_name_raw TEXT NOT NULL,
          target_name_normalized TEXT NOT NULL,
          resolved_target_entity_id INTEGER,
          resolution_status TEXT NOT NULL,
          resolution_method TEXT,
          matched_alias TEXT,
          evidence_available_at TEXT NOT NULL,
          availability_is_point_in_time INTEGER NOT NULL DEFAULT 0,
          evidence_char_start INTEGER NOT NULL,
          evidence_char_end INTEGER NOT NULL,
          evidence_text TEXT NOT NULL,
          extraction_method TEXT NOT NULL,
          extraction_rule TEXT NOT NULL,
          raw_sha256 TEXT NOT NULL,
          source_url TEXT,
          mention_count INTEGER NOT NULL DEFAULT 1,
          metadata_json TEXT,
          UNIQUE(
            extraction_run_id,
            corpus_document_id,
            source_entity_id,
            relation_type,
            target_name_normalized
          )
        );

        CREATE INDEX idx_relcand_source
          ON relation_name_candidates(
            source_entity_id,relation_type,evidence_available_at
          );
        CREATE INDEX idx_relcand_target_name
          ON relation_name_candidates(target_name_normalized);
        CREATE INDEX idx_relcand_resolution
          ON relation_name_candidates(resolution_status,relation_type);

        CREATE TABLE qa_samples(
          qa_sample_id TEXT PRIMARY KEY,
          relation_candidate_id TEXT NOT NULL,
          sample_group TEXT NOT NULL,
          deterministic_rank TEXT NOT NULL,
          manual_label TEXT,
          manual_notes TEXT,
          FOREIGN KEY(relation_candidate_id)
            REFERENCES relation_name_candidates(relation_candidate_id)
            ON DELETE CASCADE,
          UNIQUE(relation_candidate_id,sample_group)
        );
        """
    )


def eligible_documents(
    conn: sqlite3.Connection,
    cfg: dict,
) -> list[dict]:
    enabled_classes = set()
    for spec in cfg["primary_extractors"].values():
        if spec["enabled"]:
            enabled_classes.update(spec["document_classes"])

    marks = ",".join("?" for _ in enabled_classes)
    rows = conn.execute(
        f"""
        SELECT
          corpus_document_id,
          content_raw_document_id,
          accession_number,
          document_class,
          asset_id,
          subject_entity_id,
          asset_ticker,
          effective_available_at,
          availability_is_point_in_time,
          raw_sha256,
          source_url,
          normalized_char_length
        FROM corpus_documents
        WHERE document_class IN ({marks})
        ORDER BY
          effective_available_at,
          accession_number,
          corpus_document_id
        """,
        sorted(enabled_classes),
    ).fetchall()
    names = [
        "corpus_document_id",
        "content_raw_document_id",
        "accession_number",
        "document_class",
        "asset_id",
        "subject_entity_id",
        "asset_ticker",
        "effective_available_at",
        "availability_is_point_in_time",
        "raw_sha256",
        "source_url",
        "normalized_char_length",
    ]
    return [dict(zip(names, r)) for r in rows]


def plan(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    corpus_db = ROOT / cfg["corpus_db"]
    main_db = ROOT / cfg["main_db"]
    if not corpus_db.is_file():
        raise FileNotFoundError(corpus_db)
    if not main_db.is_file():
        raise FileNotFoundError(main_db)

    with sqlite3.connect(
        f"file:{corpus_db.resolve()}?mode=ro", uri=True
    ) as corpus:
        docs = eligible_documents(corpus, cfg)

    by_class = Counter(d["document_class"] for d in docs)
    missing_source_entity = sum(
        1 for d in docs if d["subject_entity_id"] is None
    )
    missing_asset = sum(1 for d in docs if d["asset_id"] is None)
    nonzero_pit = sum(
        1 for d in docs
        if int(d["availability_is_point_in_time"] or 0) != 0
    )

    with sqlite3.connect(
        f"file:{main_db.resolve()}?mode=ro", uri=True
    ) as main:
        resolver = ExactEntityResolver(main)
        alias_count = len(resolver.aliases)
        ambiguous_aliases = sum(
            1 for ids in resolver.aliases.values() if len(ids) > 1
        )

    failures = []
    if not docs:
        failures.append("no_eligible_documents")
    if missing_source_entity:
        failures.append("eligible_document_missing_source_entity")
    if missing_asset:
        failures.append("eligible_document_missing_source_asset")
    if nonzero_pit:
        failures.append("historical_corpus_contains_pit_claim")

    return {
        "status": "FAIL" if failures else "PASS",
        "failures": failures,
        "eligible_documents": len(docs),
        "document_class_counts": dict(sorted(by_class.items())),
        "eligible_source_entities": len({
            int(d["subject_entity_id"]) for d in docs
            if d["subject_entity_id"] is not None
        }),
        "eligible_assets": len({
            int(d["asset_id"]) for d in docs
            if d["asset_id"] is not None
        }),
        "exact_resolution_aliases": alias_count,
        "ambiguous_exact_aliases": ambiguous_aliases,
        "extractors": {
            k: {
                "enabled": v["enabled"],
                "relation_type": v["relation_type"],
                "document_classes": v["document_classes"],
            }
            for k, v in cfg["primary_extractors"].items()
        },
        "deferred_relation_types": cfg["deferred_extractors"],
        "strict_historical_pit": False,
        "main_db_mutated": False,
        "graph_edges_written": False,
        "next_gate": (
            "If PASS, run extraction into separate processed candidate DB. "
            "Candidates remain non-model-visible and are not promoted."
        ),
    }


def extract(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    corpus_db = ROOT / cfg["corpus_db"]
    main_db = ROOT / cfg["main_db"]
    out_db = ROOT / cfg["output_db"]

    pre = plan(config_path)
    if pre["status"] != "PASS":
        raise RuntimeError(f"candidate plan failed: {pre['failures']}")

    cfg_json = json.dumps(cfg, sort_keys=True)
    cfg_sha = hashlib.sha256(cfg_json.encode("utf-8")).hexdigest()
    run_id = stable_id("relcandrun", cfg["version"], cfg_sha)

    with sqlite3.connect(
        f"file:{main_db.resolve()}?mode=ro", uri=True
    ) as main:
        resolver = ExactEntityResolver(main)

        with sqlite3.connect(
            f"file:{corpus_db.resolve()}?mode=ro", uri=True
        ) as corpus:
            docs = eligible_documents(corpus, cfg)

            out_db.parent.mkdir(parents=True, exist_ok=True)
            if out_db.exists():
                out_db.unlink()

            result = {
                "status": "PASS",
                "extraction_run_id": run_id,
                "documents_scanned": 0,
                "documents_with_candidates": 0,
                "candidates_written": 0,
                "by_relation_type": Counter(),
                "by_extraction_method": Counter(),
                "by_resolution_status": Counter(),
                "self_relation_candidates_rejected": 0,
                "duplicate_mentions_collapsed": 0,
                "strict_historical_pit": False,
                "main_db_mutated": False,
                "graph_edges_written": False,
                "relations_promoted": False,
            }

            with sqlite3.connect(out_db) as out:
                out.execute("PRAGMA foreign_keys=ON")
                ensure_schema(out)
                out.execute(
                    """
                    INSERT INTO extraction_runs(
                      extraction_run_id,extraction_version,status,
                      configuration_json,configuration_sha256
                    ) VALUES (?,?,?,?,?)
                    """,
                    (run_id, cfg["version"], "running", cfg_json, cfg_sha),
                )
                out.commit()

                try:
                    for d in docs:
                        text = reconstruct_document(
                            corpus,
                            d["corpus_document_id"],
                            int(d["normalized_char_length"]),
                        )
                        result["documents_scanned"] += 1

                        candidates: list[Candidate] = []
                        if d["document_class"] == "subsidiary_exhibit":
                            candidates = extract_exhibit21(text)
                        elif d["document_class"] in {
                            "material_contract_exhibit",
                            "transaction_exhibit",
                        }:
                            candidates = extract_agreement_counterparties(
                                text,
                                prefix_chars=cfg["primary_extractors"][
                                    "agreement_counterparty_v001"
                                ]["search_prefix_chars"],
                            )

                        if not candidates:
                            continue

                        written_this_doc = 0
                        grouped: dict[tuple[str, str], list[Candidate]] = defaultdict(list)
                        for c in candidates:
                            grouped[
                                (
                                    c.relation_type,
                                    normalize_entity_name(c.target_name_raw),
                                )
                            ].append(c)

                        for (relation_type, target_norm), mentions in grouped.items():
                            if not target_norm:
                                continue
                            c = mentions[0]
                            if len(mentions) > 1:
                                result["duplicate_mentions_collapsed"] += (
                                    len(mentions) - 1
                                )

                            resolution = resolver.resolve(c.target_name_raw)
                            if (
                                resolution.entity_id is not None
                                and int(resolution.entity_id)
                                == int(d["subject_entity_id"])
                            ):
                                result[
                                    "self_relation_candidates_rejected"
                                ] += 1
                                continue

                            cid = stable_id(
                                "relcand",
                                run_id,
                                d["corpus_document_id"],
                                d["subject_entity_id"],
                                relation_type,
                                target_norm,
                            )
                            out.execute(
                                """
                                INSERT INTO relation_name_candidates(
                                  relation_candidate_id,extraction_run_id,
                                  corpus_document_id,content_raw_document_id,
                                  accession_number,document_class,
                                  source_asset_id,source_entity_id,source_ticker,
                                  relation_type,target_name_raw,
                                  target_name_normalized,
                                  resolved_target_entity_id,resolution_status,
                                  resolution_method,matched_alias,
                                  evidence_available_at,
                                  availability_is_point_in_time,
                                  evidence_char_start,evidence_char_end,
                                  evidence_text,extraction_method,
                                  extraction_rule,raw_sha256,source_url,
                                  mention_count,metadata_json
                                ) VALUES (
                                  ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                                )
                                """,
                                (
                                    cid, run_id, d["corpus_document_id"],
                                    d["content_raw_document_id"],
                                    d["accession_number"],
                                    d["document_class"],
                                    int(d["asset_id"]),
                                    int(d["subject_entity_id"]),
                                    d["asset_ticker"],
                                    relation_type,
                                    c.target_name_raw,
                                    target_norm,
                                    resolution.entity_id,
                                    resolution.status,
                                    resolution.method,
                                    resolution.matched_alias,
                                    d["effective_available_at"],
                                    0,
                                    int(c.evidence_start),
                                    int(c.evidence_end),
                                    c.evidence_text,
                                    c.extraction_method,
                                    c.extraction_rule,
                                    d["raw_sha256"],
                                    d["source_url"],
                                    len(mentions),
                                    json.dumps({
                                        "candidate_is_model_visible": False,
                                        "candidate_is_graph_edge": False,
                                        "market_direction_assigned": False,
                                        "market_weight_assigned": False,
                                    }, sort_keys=True),
                                ),
                            )
                            result["candidates_written"] += 1
                            result["by_relation_type"][relation_type] += 1
                            result["by_extraction_method"][
                                c.extraction_method
                            ] += 1
                            result["by_resolution_status"][
                                resolution.status
                            ] += 1
                            written_this_doc += 1

                        if written_this_doc:
                            result["documents_with_candidates"] += 1

                    out.execute(
                        """
                        UPDATE extraction_runs
                        SET finished_at=CURRENT_TIMESTAMP,status='completed',
                            documents_scanned=?,candidates_written=?
                        WHERE extraction_run_id=?
                        """,
                        (
                            result["documents_scanned"],
                            result["candidates_written"],
                            run_id,
                        ),
                    )
                    out.commit()
                except Exception as exc:
                    out.rollback()
                    out.execute(
                        """
                        UPDATE extraction_runs
                        SET finished_at=CURRENT_TIMESTAMP,status='failed',
                            error_json=?
                        WHERE extraction_run_id=?
                        """,
                        (
                            json.dumps([f"{type(exc).__name__}: {exc}"]),
                            run_id,
                        ),
                    )
                    out.commit()
                    raise

    for key in (
        "by_relation_type",
        "by_extraction_method",
        "by_resolution_status",
    ):
        result[key] = dict(sorted(result[key].items()))
    return result


def deterministic_qa_sample(
    config_path: Path = DEFAULT_CONFIG,
) -> dict:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    out_db = ROOT / cfg["output_db"]
    if not out_db.is_file():
        raise FileNotFoundError(out_db)

    per_extractor = int(cfg["qa"]["sample_per_extractor"])
    per_status = int(cfg["qa"]["sample_per_resolution_status"])

    with sqlite3.connect(out_db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute("DELETE FROM qa_samples")

        candidates = conn.execute(
            """
            SELECT
              relation_candidate_id,extraction_method,resolution_status
            FROM relation_name_candidates
            ORDER BY relation_candidate_id
            """
        ).fetchall()

        groups: dict[str, list[str]] = defaultdict(list)
        for cid, method, status in candidates:
            groups[f"extractor:{method}"].append(str(cid))
            groups[f"resolution:{status}"].append(str(cid))

        inserted = 0
        for group, ids in sorted(groups.items()):
            limit = (
                per_extractor
                if group.startswith("extractor:")
                else per_status
            )
            ranked = sorted(
                ids,
                key=lambda x: hashlib.sha256(
                    f"{group}\0{x}".encode("utf-8")
                ).hexdigest(),
            )[:limit]
            for rank, cid in enumerate(ranked):
                sid = stable_id("qasample", group, cid)
                conn.execute(
                    """
                    INSERT OR IGNORE INTO qa_samples(
                      qa_sample_id,relation_candidate_id,
                      sample_group,deterministic_rank
                    ) VALUES (?,?,?,?)
                    """,
                    (sid, cid, group, f"{rank:06d}"),
                )
                inserted += 1
        conn.commit()

        rows = conn.execute(
            """
            SELECT
              q.sample_group,q.deterministic_rank,
              c.relation_candidate_id,c.accession_number,
              c.document_class,c.source_ticker,c.relation_type,
              c.target_name_raw,c.resolution_status,
              c.resolved_target_entity_id,c.evidence_available_at,
              c.evidence_text,c.extraction_method,c.extraction_rule,
              c.source_url
            FROM qa_samples q
            JOIN relation_name_candidates c
              ON c.relation_candidate_id=q.relation_candidate_id
            ORDER BY q.sample_group,q.deterministic_rank
            """
        ).fetchall()

    names = [
        "sample_group","deterministic_rank","relation_candidate_id",
        "accession_number","document_class","source_ticker",
        "relation_type","target_name_raw","resolution_status",
        "resolved_target_entity_id","evidence_available_at",
        "evidence_text","extraction_method","extraction_rule","source_url",
    ]
    sample = [dict(zip(names, row)) for row in rows]
    report_dir = ROOT / cfg["report_dir"]
    report_dir.mkdir(parents=True, exist_ok=True)
    path = report_dir / "qa_sample.json"
    path.write_text(json.dumps(sample, indent=2) + "\n", encoding="utf-8")

    return {
        "status": "PASS",
        "sample_rows": len(sample),
        "sample_file": str(path),
        "groups": dict(Counter(x["sample_group"] for x in sample)),
        "manual_labels_required_before_promotion": True,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument(
        "--stage",
        required=True,
        choices=("plan", "extract", "qa-sample"),
    )
    a = p.parse_args()
    if a.stage == "plan":
        result = plan(a.config)
    elif a.stage == "extract":
        result = extract(a.config)
    else:
        result = deterministic_qa_sample(a.config)
    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

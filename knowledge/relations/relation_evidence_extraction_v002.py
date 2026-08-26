from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path

from knowledge.entities.exact_entity_resolution_v002 import (
    ExactResolverV002,
    normalize_name,
)


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/event_graph_relation_evidence_v002.json"

# Ordered longest/specific first. The V001 regex matched "Company" before
# "Company LLC" and "S.A." before "S.A. de C.V."; V002 explicitly prevents it.
LEGAL_SUFFIX_PATTERNS = [
    r"S\.?\s+de\s+R\.?L\.?\s+de\s+C\.?V\.?",
    r"S\.?A\.?\s+de\s+C\.?V\.?",
    r"S\.?A\.?S\.?",
    r"S\.?p\.?A\.?",
    r"Pty\.?\s+Limited",
    r"Pty\.?\s+Ltd\.?",
    r"Pte\.?\s+Ltd\.?",
    r"Unlimited\s+Company",
    r"Limited\s+Liability\s+Company",
    r"Limited\s+Partnership",
    r"N\.?A\.?",
    r"L\.?L\.?C\.?",
    r"L\.?L\.?P\.?",
    r"L\.?P\.?",
    r"Incorporated",
    r"Corporation",
    r"Company",
    r"Limited",
    r"Inc\.?",
    r"Corp\.?",
    r"Co\.?",
    r"Ltd\.?",
    r"PLC",
    r"GmbH",
    r"Sarl",
    r"SARL",
    r"Sàrl",
    r"B\.?V\.?",
    r"N\.?V\.?",
    r"K\.?K\.?",
    r"Kft\.?",
    r"AG",
    r"SE",
    r"AB",
    r"Oy",
    r"A/S",
    r"AS",
    r"LLC",
    r"LLP",
    r"LP",
    r"SA",
    r"NV",
    r"BV",
]
LEGAL_SUFFIX = "(?:" + "|".join(LEGAL_SUFFIX_PATTERNS) + ")"
LEGAL_SUFFIX_SCAN_RE = re.compile(
    r"(?<!\\w)" + LEGAL_SUFFIX + r"(?!\\w)"
)

BAD_NAME_PHRASES = re.compile(
    r"""
    (?:
      \btable\s+of\s+contents\b
      |\barticle\s+\d+\b
      |\bsection\s+\d
      |\brepresentations\s+and\s+warranties\b
      |\bwhere\s+incorporated\b
      |\borganized\s+or\s+incorporated\b
      |\bplace\s+of\s+incorporation\b
      |\bstate\s+or\s+country\b
      |\bjurisdiction\s+of\b
      |\bshareholder\s+register\b
      |\bboard\s+approvals?\b
      |\bconduct\s+of\s+business\b
      |\bclosing,\s+the\s+company\b
      |\bmerger,\s+the\s+company\b
      |\bacquiror,\s+the\s+company\b
    )
    """,
    re.I | re.X,
)
ROLE_ONLY = {
    "company",
    "the company",
    "acquiror",
    "the acquiror",
    "bidder",
    "offeror",
    "grantor",
    "seller",
    "buyer",
    "borrower",
    "lender",
}
COUNTRY_PREFIX_BAD = re.compile(
    r"^(?:shanghai|europe|singapore)\)\s+",
    re.I,
)
ADDRESS_LIKE = re.compile(
    r"^\s*\d{1,6}\s+\S+.*(?:street|avenue|road|boulevard|drive)\b",
    re.I,
)

PARTY_MARKER = re.compile(
    r"\b(?:by\s+and\s+between|by\s+and\s+among|among\s+the\s+following)\b",
    re.I,
)
BLOCK_STOP = re.compile(
    r"\b(?:RECITALS?|WHEREAS|WITNESSETH|NOW,\s+THEREFORE)\b",
    re.I,
)
TABLE_OF_CONTENTS = re.compile(r"\bTABLE\s+OF\s+CONTENTS\b", re.I)

@dataclass(frozen=True)
class EvidenceClaim:
    claim_kind: str
    named_entity_raw: str
    start: int
    end: int
    evidence_text: str
    method: str
    quality_flags: tuple[str, ...] = ()


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join(str(x) for x in parts).encode()
    return prefix + "_" + hashlib.sha256(raw).hexdigest()


def clean(value: str) -> str:
    # Keep parentheses here because EX-21 ownership annotations such as "(5)"
    # are parsed after the legal name. Entity-specific cleanup happens later.
    return re.sub(r"\s+", " ", str(value or "")).strip(
        " \t\r\n,;:."
    )


def quality_flags(name: str) -> tuple[str, ...]:
    flags = []
    n = clean(name)
    norm = normalize_name(n)
    if BAD_NAME_PHRASES.search(n):
        flags.append("heading_or_toc_language")
    if norm in ROLE_ONLY:
        flags.append("generic_legal_role")
    if COUNTRY_PREFIX_BAD.search(n):
        flags.append("broken_parenthetical_prefix")
    if ADDRESS_LIKE.search(n):
        flags.append("address_prefix")
    if re.match(r"^\d", n):
        flags.append("numeric_prefix")
    if n.count("(") != n.count(")"):
        flags.append("unbalanced_parentheses")
    if len(n) < 3 or len(n) > 190:
        flags.append("implausible_length")
    return tuple(sorted(set(flags)))


def reconstruct_document(
    conn: sqlite3.Connection,
    document_id: str,
    expected_length: int,
) -> str:
    rows = conn.execute(
        """
        SELECT char_start,char_end,chunk_text
        FROM corpus_chunks
        WHERE corpus_document_id=?
        ORDER BY chunk_index
        """,
        (document_id,),
    ).fetchall()
    if not rows:
        return ""
    chars = [" "] * int(expected_length)
    written = [False] * int(expected_length)
    for start, end, body in rows:
        start, end, body = int(start), int(end), str(body)
        if len(body) != end - start:
            raise RuntimeError("chunk offset mismatch")
        for i, ch in enumerate(body, start):
            if i >= len(chars):
                raise RuntimeError("chunk beyond document length")
            if not written[i]:
                chars[i] = ch
                written[i] = True
            elif chars[i] != ch:
                raise RuntimeError("overlapping chunks disagree")
    return "".join(chars)



def _name_through_last_legal_suffix(value: str) -> str | None:
    """
    Return text from the beginning through the last recognized legal suffix.
    This preserves compound endings such as Company LLC, S.A. de C.V. and
    Trust Company, N.A.
    """
    text = clean(value)
    matches = list(LEGAL_SUFFIX_SCAN_RE.finditer(text))
    if not matches:
        return None
    last = max(matches, key=lambda m: m.end())
    name = clean(text[: last.end()])
    return name or None


def _truncate_party_descriptor(segment: str) -> str:
    """
    Remove jurisdiction/role prose that follows a legal party name.
    """
    text = clean(segment)
    cuts = []
    patterns = [
        r',\s*(?:a|an)\s+(?:[A-Z][A-Za-z.\- ]{0,45}\s+)?'
        r'(?:corporation|limited liability company|company|partnership|'
        r'limited partnership|banking corporation|trust company)\b',
        r',\s*as\s+[A-Z][A-Za-z \-]{1,60}\b',
        r'\(\s*(?:the\s+)?[“"\']?[A-Za-z][^)]{0,70}\)',
    ]
    for pattern in patterns:
        m = re.search(pattern, text, flags=re.I)
        if m:
            cuts.append(m.start())
    if cuts:
        text = text[: min(cuts)]
    return clean(text)


def extract_ex21(text: str) -> list[EvidenceClaim]:
    out = []
    offset = 0
    seen = set()
    for raw_line in text.splitlines(keepends=True):
        line = raw_line.rstrip("\r\n")
        stripped = clean(line)
        name = _name_through_last_legal_suffix(stripped)
        if name is not None:
            flags = quality_flags(name)
            trailing = stripped[len(name):].strip()
            trailing_ok = bool(
                re.fullmatch(
                    r"(?:\s*\(\d+\))*\s*(?:\d+(?:\.\d+)?)?\s*",
                    trailing,
                )
            )
            if not flags and trailing_ok:
                norm = normalize_name(name)
                if norm not in seen:
                    local = line.find(name)
                    if local >= 0:
                        start = offset + local
                        end = start + len(name)
                        out.append(
                            EvidenceClaim(
                                "reported_subsidiary_of_registrant",
                                name,
                                start,
                                end,
                                text[max(0,start-100):min(len(text),end+160)].strip(),
                                "ex21_reported_subsidiary_v002",
                            )
                        )
                        seen.add(norm)
        offset += len(raw_line)
    return out


def _party_block(text: str, max_marker_offset: int, max_chars: int):
    for marker in PARTY_MARKER.finditer(text):
        if marker.start() > max_marker_offset:
            break
        # A marker inside/after a table of contents is unsafe in V002.
        preceding = text[max(0, marker.start()-1200):marker.start()]
        if TABLE_OF_CONTENTS.search(preceding):
            continue

        tail = text[marker.end():min(len(text), marker.end()+max_chars)]
        stop = BLOCK_STOP.search(tail)
        if stop:
            tail = tail[:stop.start()]
        # If a TOC appears before recitals, reject this block entirely.
        if TABLE_OF_CONTENTS.search(tail):
            continue
        if len(tail.strip()) < 5:
            continue
        return marker.end(), tail
    return None


def extract_contract_parties(
    text: str,
    max_marker_offset: int = 16000,
    max_chars: int = 2600,
) -> list[EvidenceClaim]:
    block = _party_block(text, max_marker_offset, max_chars)
    if block is None:
        return []
    base, body = block

    segments = re.split(r"\s+\band\b\s+", body, flags=re.I)
    out = []
    seen = set()
    cursor = 0

    for segment in segments:
        seg_start = body.find(segment, cursor)
        if seg_start < 0:
            seg_start = cursor
        cursor = seg_start + len(segment)

        candidate_region = _truncate_party_descriptor(segment)
        name = _name_through_last_legal_suffix(candidate_region)
        if name is None:
            continue

        flags = quality_flags(name)
        if flags:
            continue
        if re.fullmatch(
            r"(?:Delaware|Pennsylvania|New Jersey|New York)\s+Corporation",
            name,
            re.I,
        ):
            continue

        norm = normalize_name(name)
        if norm in seen:
            continue
        seen.add(norm)

        local = segment.find(name)
        if local < 0:
            continue
        start = base + seg_start + local
        end = start + len(name)
        out.append(
            EvidenceClaim(
                "contract_party_mention",
                name,
                start,
                end,
                text[max(0,start-260):min(len(text),end+420)].strip(),
                "agreement_party_set_v002",
            )
        )
    return out


def eligible_documents(conn: sqlite3.Connection) -> list[dict]:
    rows = conn.execute(
        """
        SELECT corpus_document_id,content_raw_document_id,accession_number,
               document_class,asset_id,subject_entity_id,asset_ticker,
               effective_available_at,availability_is_point_in_time,
               raw_sha256,source_url,normalized_char_length
        FROM corpus_documents
        WHERE document_class IN (
          'subsidiary_exhibit',
          'material_contract_exhibit',
          'transaction_exhibit'
        )
        ORDER BY effective_available_at,accession_number,corpus_document_id
        """
    ).fetchall()
    names = [
        "corpus_document_id","content_raw_document_id","accession_number",
        "document_class","asset_id","subject_entity_id","asset_ticker",
        "effective_available_at","availability_is_point_in_time",
        "raw_sha256","source_url","normalized_char_length",
    ]
    return [dict(zip(names,r)) for r in rows]


def ensure_schema(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE extraction_runs(
      extraction_run_id TEXT PRIMARY KEY,
      version TEXT NOT NULL,
      status TEXT NOT NULL,
      configuration_json TEXT NOT NULL,
      configuration_sha256 TEXT NOT NULL,
      started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      finished_at TEXT,
      documents_scanned INTEGER NOT NULL DEFAULT 0,
      claims_written INTEGER NOT NULL DEFAULT 0,
      error_json TEXT
    );

    CREATE TABLE evidence_claims(
      evidence_claim_id TEXT PRIMARY KEY,
      extraction_run_id TEXT NOT NULL,
      claim_kind TEXT NOT NULL,
      corpus_document_id TEXT NOT NULL,
      content_raw_document_id TEXT NOT NULL,
      accession_number TEXT NOT NULL,
      document_class TEXT NOT NULL,
      registrant_asset_id INTEGER NOT NULL,
      registrant_entity_id INTEGER NOT NULL,
      registrant_ticker TEXT,
      named_entity_raw TEXT NOT NULL,
      named_entity_normalized TEXT NOT NULL,
      resolved_named_entity_id INTEGER,
      resolution_status TEXT NOT NULL,
      resolution_method TEXT,
      matched_alias TEXT,
      evidence_available_at TEXT NOT NULL,
      availability_is_point_in_time INTEGER NOT NULL DEFAULT 0,
      evidence_char_start INTEGER NOT NULL,
      evidence_char_end INTEGER NOT NULL,
      evidence_text TEXT NOT NULL,
      extraction_method TEXT NOT NULL,
      quality_flags_json TEXT NOT NULL,
      raw_sha256 TEXT NOT NULL,
      source_url TEXT,
      edge_ready INTEGER NOT NULL DEFAULT 0,
      metadata_json TEXT,
      UNIQUE(
        extraction_run_id,claim_kind,corpus_document_id,
        named_entity_normalized
      )
    );

    CREATE INDEX idx_claim_kind
      ON evidence_claims(claim_kind,evidence_available_at);
    CREATE INDEX idx_claim_resolution
      ON evidence_claims(resolution_status,claim_kind);

    CREATE TABLE contract_party_sets(
      party_set_id TEXT PRIMARY KEY,
      extraction_run_id TEXT NOT NULL,
      corpus_document_id TEXT NOT NULL,
      accession_number TEXT NOT NULL,
      registrant_entity_id INTEGER NOT NULL,
      evidence_available_at TEXT NOT NULL,
      party_count INTEGER NOT NULL,
      metadata_json TEXT,
      UNIQUE(extraction_run_id,corpus_document_id)
    );

    CREATE TABLE qa_samples(
      qa_sample_id TEXT PRIMARY KEY,
      evidence_claim_id TEXT NOT NULL,
      sample_group TEXT NOT NULL,
      deterministic_rank TEXT NOT NULL,
      manual_label TEXT,
      manual_notes TEXT,
      UNIQUE(evidence_claim_id,sample_group)
    );
    """)


def plan(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(config_path.read_text())
    corpus_db = ROOT / cfg["corpus_db"]
    main_db = ROOT / cfg["main_db"]
    with sqlite3.connect(
        f"file:{corpus_db.resolve()}?mode=ro", uri=True
    ) as corpus:
        docs = eligible_documents(corpus)
    with sqlite3.connect(
        f"file:{main_db.resolve()}?mode=ro", uri=True
    ) as main:
        resolver = ExactResolverV002(main)

    failures = []
    if not docs:
        failures.append("no_eligible_documents")
    if any(d["asset_id"] is None for d in docs):
        failures.append("eligible_document_missing_asset")
    if any(d["subject_entity_id"] is None for d in docs):
        failures.append("eligible_document_missing_registrant_entity")
    if any(int(d["availability_is_point_in_time"] or 0) != 0 for d in docs):
        failures.append("historical_corpus_has_pit_claim")

    return {
        "status": "FAIL" if failures else "PASS",
        "failures": failures,
        "eligible_documents": len(docs),
        "by_document_class": dict(sorted(Counter(
            d["document_class"] for d in docs
        ).items())),
        "registrant_entities": len(set(
            int(d["subject_entity_id"]) for d in docs
        )),
        "assets": len(set(int(d["asset_id"]) for d in docs)),
        "resolver_aliases": len(resolver.aliases),
        "ambiguous_aliases": sum(
            1 for ids in resolver.aliases.values() if len(ids)>1
        ),
        "semantic_contract": {
            "ex21_direct_parent_edge": False,
            "ex21_claim": "reported_subsidiary_of_registrant",
            "registrant_assumed_contract_party": False,
            "contract_output": "document_party_set",
        },
        "strict_historical_pit": False,
        "main_db_mutated": False,
        "graph_edges_written": False,
    }


def extract(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(config_path.read_text())
    pre = plan(config_path)
    if pre["status"] != "PASS":
        raise RuntimeError(pre["failures"])

    corpus_db = ROOT / cfg["corpus_db"]
    main_db = ROOT / cfg["main_db"]
    out_db = ROOT / cfg["output_db"]
    cfg_json = json.dumps(cfg, sort_keys=True)
    cfg_sha = hashlib.sha256(cfg_json.encode()).hexdigest()
    run_id = stable_id("relevrun", cfg["version"], cfg_sha)

    with sqlite3.connect(
        f"file:{main_db.resolve()}?mode=ro", uri=True
    ) as main:
        resolver = ExactResolverV002(main)
        with sqlite3.connect(
            f"file:{corpus_db.resolve()}?mode=ro", uri=True
        ) as corpus:
            docs = eligible_documents(corpus)
            out_db.parent.mkdir(parents=True, exist_ok=True)
            if out_db.exists():
                out_db.unlink()

            result = {
                "status":"PASS",
                "run_id":run_id,
                "documents_scanned":0,
                "documents_with_claims":0,
                "claims_written":0,
                "by_claim_kind":Counter(),
                "by_resolution_status":Counter(),
                "contract_party_sets":0,
                "self_name_claims":0,
                "strict_historical_pit":False,
                "main_db_mutated":False,
                "graph_edges_written":False,
            }

            with sqlite3.connect(out_db) as out:
                ensure_schema(out)
                out.execute(
                    """INSERT INTO extraction_runs(
                       extraction_run_id,version,status,
                       configuration_json,configuration_sha256
                    ) VALUES (?,?,?,?,?)""",
                    (run_id,cfg["version"],"running",cfg_json,cfg_sha)
                )
                out.commit()
                try:
                    for d in docs:
                        text = reconstruct_document(
                            corpus,d["corpus_document_id"],
                            int(d["normalized_char_length"])
                        )
                        result["documents_scanned"] += 1
                        if d["document_class"]=="subsidiary_exhibit":
                            claims=extract_ex21(text)
                        else:
                            spec=cfg["extractors"]["agreement_party_set_v002"]
                            claims=extract_contract_parties(
                                text,
                                int(spec["max_marker_offset"]),
                                int(spec["max_party_block_chars"]),
                            )

                        if not claims:
                            continue
                        result["documents_with_claims"] += 1

                        written_claim_ids=[]
                        for c in claims:
                            resolution=resolver.resolve(c.named_entity_raw)
                            if (
                                resolution.entity_id is not None
                                and int(resolution.entity_id)
                                == int(d["subject_entity_id"])
                            ):
                                result["self_name_claims"] += 1

                            cid=stable_id(
                                "relev",run_id,d["corpus_document_id"],
                                c.claim_kind,
                                normalize_name(c.named_entity_raw)
                            )
                            out.execute(
                                """INSERT OR IGNORE INTO evidence_claims(
                                  evidence_claim_id,extraction_run_id,
                                  claim_kind,corpus_document_id,
                                  content_raw_document_id,accession_number,
                                  document_class,registrant_asset_id,
                                  registrant_entity_id,registrant_ticker,
                                  named_entity_raw,named_entity_normalized,
                                  resolved_named_entity_id,resolution_status,
                                  resolution_method,matched_alias,
                                  evidence_available_at,
                                  availability_is_point_in_time,
                                  evidence_char_start,evidence_char_end,
                                  evidence_text,extraction_method,
                                  quality_flags_json,raw_sha256,source_url,
                                  edge_ready,metadata_json
                                ) VALUES (
                                  ?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?
                                )""",
                                (
                                    cid,run_id,c.claim_kind,
                                    d["corpus_document_id"],
                                    d["content_raw_document_id"],
                                    d["accession_number"],d["document_class"],
                                    int(d["asset_id"]),
                                    int(d["subject_entity_id"]),
                                    d["asset_ticker"],c.named_entity_raw,
                                    normalize_name(c.named_entity_raw),
                                    resolution.entity_id,resolution.status,
                                    resolution.method,resolution.alias,
                                    d["effective_available_at"],0,
                                    c.start,c.end,c.evidence_text,c.method,
                                    json.dumps(list(c.quality_flags)),
                                    d["raw_sha256"],d["source_url"],0,
                                    json.dumps({
                                      "registrant_is_evidence_provenance":True,
                                      "registrant_assumed_party":False,
                                      "direct_parenthood_asserted":False,
                                    },sort_keys=True),
                                )
                            )
                            if out.total_changes:
                                written_claim_ids.append(cid)
                            result["claims_written"] += 1
                            result["by_claim_kind"][c.claim_kind] += 1
                            result["by_resolution_status"][
                                resolution.status
                            ] += 1

                        if (
                            d["document_class"] in {
                                "material_contract_exhibit",
                                "transaction_exhibit",
                            }
                            and written_claim_ids
                        ):
                            psid=stable_id(
                                "partyset",run_id,d["corpus_document_id"]
                            )
                            out.execute(
                                """INSERT OR IGNORE INTO contract_party_sets(
                                  party_set_id,extraction_run_id,
                                  corpus_document_id,accession_number,
                                  registrant_entity_id,evidence_available_at,
                                  party_count,metadata_json
                                ) VALUES (?,?,?,?,?,?,?,?)""",
                                (
                                    psid,run_id,d["corpus_document_id"],
                                    d["accession_number"],
                                    int(d["subject_entity_id"]),
                                    d["effective_available_at"],
                                    len(written_claim_ids),
                                    json.dumps({
                                      "pairwise_edges_created":False
                                    })
                                )
                            )
                            result["contract_party_sets"] += 1

                    out.execute(
                        """UPDATE extraction_runs
                           SET status='completed',
                               finished_at=CURRENT_TIMESTAMP,
                               documents_scanned=?,claims_written=?
                           WHERE extraction_run_id=?""",
                        (
                            result["documents_scanned"],
                            result["claims_written"],run_id
                        )
                    )
                    out.commit()
                except Exception as exc:
                    out.rollback()
                    raise

    result["by_claim_kind"]=dict(sorted(result["by_claim_kind"].items()))
    result["by_resolution_status"]=dict(
        sorted(result["by_resolution_status"].items())
    )
    return result


def qa_sample(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg=json.loads(config_path.read_text())
    db=ROOT/cfg["output_db"]
    per=int(cfg["qa"]["sample_per_claim_kind"])
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM qa_samples")
        rows=conn.execute(
            """SELECT evidence_claim_id,claim_kind
               FROM evidence_claims ORDER BY evidence_claim_id"""
        ).fetchall()
        groups=defaultdict(list)
        for cid,kind in rows:
            groups[str(kind)].append(str(cid))
        for group,ids in groups.items():
            ranked=sorted(
                ids,
                key=lambda x:hashlib.sha256(
                    f"{group}\0{x}".encode()
                ).hexdigest()
            )[:per]
            for rank,cid in enumerate(ranked):
                conn.execute(
                    """INSERT INTO qa_samples(
                       qa_sample_id,evidence_claim_id,
                       sample_group,deterministic_rank
                    ) VALUES (?,?,?,?)""",
                    (
                        stable_id("qas",group,cid),cid,group,
                        f"{rank:06d}"
                    )
                )
        conn.commit()
        sample=conn.execute(
            """SELECT q.sample_group,q.deterministic_rank,
                      c.evidence_claim_id,c.document_class,
                      c.registrant_ticker,c.claim_kind,
                      c.named_entity_raw,c.resolution_status,
                      c.resolved_named_entity_id,c.evidence_available_at,
                      c.evidence_text,c.extraction_method,c.source_url
               FROM qa_samples q
               JOIN evidence_claims c
                 ON c.evidence_claim_id=q.evidence_claim_id
               ORDER BY q.sample_group,q.deterministic_rank"""
        ).fetchall()
    names=[
        "sample_group","deterministic_rank","evidence_claim_id",
        "document_class","registrant_ticker","claim_kind",
        "named_entity_raw","resolution_status",
        "resolved_named_entity_id","evidence_available_at",
        "evidence_text","extraction_method","source_url"
    ]
    payload=[dict(zip(names,r)) for r in sample]
    report_dir=ROOT/cfg["report_dir"]
    report_dir.mkdir(parents=True,exist_ok=True)
    path=report_dir/"qa_sample.json"
    path.write_text(json.dumps(payload,indent=2)+"\n")
    return {
      "status":"PASS",
      "sample_rows":len(payload),
      "sample_file":str(path),
      "manual_review_required":True,
      "promotion_allowed":False,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    p.add_argument("--stage",required=True,choices=("plan","extract","qa-sample"))
    a=p.parse_args()
    if a.stage=="plan":
        r=plan(a.config)
    elif a.stage=="extract":
        r=extract(a.config)
    else:
        r=qa_sample(a.config)
    print(json.dumps(r,indent=2))


if __name__=="__main__":
    main()

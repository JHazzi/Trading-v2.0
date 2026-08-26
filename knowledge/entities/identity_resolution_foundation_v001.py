from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/event_graph_identity_resolution_foundation_v001.json"


def normalize(value: str | None) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = text.replace("\u00a0", " ")
    text = re.sub(r"\s+", " ", text).strip()
    return text.casefold()


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join(str(x) for x in parts).encode("utf-8")
    return prefix + "_" + hashlib.sha256(raw).hexdigest()


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg["version"] != "event_graph_identity_resolution_foundation_v001":
        raise ValueError("unexpected foundation version")
    c = cfg["classification_contract"]
    forbidden_true = [
        "reference_equivalence_is_identity_verdict",
        "jurisdiction_hierarchy_is_identity_verdict",
        "same_accession_is_identity_verdict",
        "temporal_nonoverlap_is_identity_verdict",
        "dba_cross_match_is_identity_verdict",
        "automatic_merge","automatic_split","automatic_row_exclusion",
        "llm_identity_decision","fuzzy_identity_matching",
    ]
    if any(bool(c[x]) for x in forbidden_true):
        raise ValueError("identity foundation hard guard disabled")
    return cfg


def load_reference(cfg: dict) -> dict:
    ref = json.loads((ROOT / cfg["jurisdiction_reference"]).read_text(encoding="utf-8"))
    if not ref["semantics"]["candidate_reference_only"]:
        raise ValueError("jurisdiction reference must remain candidate-only")
    if ref["semantics"]["writeback_allowed"]:
        raise ValueError("jurisdiction writeback is forbidden in V001")
    return ref


def equivalence_index(ref: dict) -> dict[str, dict[str, Any]]:
    out = {}
    for rec in ref["equivalence_candidates"]:
        for alias in rec["aliases"]:
            n = normalize(alias)
            if n in out and out[n]["concept_id"] != rec["concept_id"]:
                raise ValueError(f"jurisdiction alias collision: {n}")
            out[n] = rec
    return out


def hierarchy_index(ref: dict) -> tuple[dict[str, str], dict[str, set[str]]]:
    child_parent = {}
    parent_children = {}
    for parent, rec in ref["hierarchy_candidates"].items():
        p = normalize(parent)
        children = {normalize(x) for x in rec["children"]}
        parent_children[p] = children
        for child in children:
            if child in child_parent and child_parent[child] != p:
                raise ValueError(f"jurisdiction hierarchy collision: {child}")
            child_parent[child] = p
    return child_parent, parent_children


def reference_relation(left: str, right: str, ref: dict) -> dict[str, Any]:
    l = normalize(left)
    r = normalize(right)
    eq = equivalence_index(ref)
    child_parent, _ = hierarchy_index(ref)

    if l == r:
        return {"kind":"exact_same_raw_normalized","concept_id":None,"context_required":False}
    if l in eq and r in eq and eq[l]["concept_id"] == eq[r]["concept_id"]:
        rec = eq[l]
        return {
            "kind":"reference_equivalent_candidate",
            "concept_id":rec["concept_id"],
            "candidate_kind":rec["candidate_kind"],
            "context_required":bool(rec.get("context_required")),
        }
    if child_parent.get(l) == r or child_parent.get(r) == l:
        return {
            "kind":"hierarchical_granularity_candidate",
            "parent": r if child_parent.get(l) == r else l,
            "child": l if child_parent.get(l) == r else r,
            "context_required":True,
        }
    return {"kind":"distinct_or_unmapped_reference","context_required":True}


def classify_pair(pair: dict, ref: dict) -> dict[str, Any]:
    rr = reference_relation(
        pair["left_jurisdiction"],
        pair["right_jurisdiction"],
        ref,
    )
    shared = len(pair.get("shared_accessions") or [])
    temporal = str(pair["temporal_relation"])

    if rr["kind"] == "reference_equivalent_candidate":
        review_class = "reference_equivalent_candidate"
    elif rr["kind"] == "hierarchical_granularity_candidate":
        review_class = "hierarchical_granularity_candidate"
    elif shared > 0:
        review_class = "same_accession_distinct_or_source_error"
    elif temporal in {
        "left_before_right_no_overlap",
        "right_before_left_no_overlap",
    }:
        review_class = "temporal_rejurisdiction_or_reporting_change_candidate"
    else:
        review_class = "unresolved_overlap_distinct_jurisdiction"

    return {
        "review_class": review_class,
        "reference_relation": rr,
        "shared_accession_count": shared,
        "temporal_relation": temporal,
        "dba_cross_match_count": int(pair.get("dba_cross_match_count") or 0),
        "identity_verdict": None,
        "automatic_merge": False,
        "automatic_split": False,
    }


def row_quality_reason(name: str, jurisdiction: str | None, cfg: dict) -> list[str]:
    reasons = []
    text = str(name or "")
    for pattern in cfg["row_quality_rules"]["section_heading_patterns"]:
        if re.search(pattern, text, flags=re.I):
            reasons.append("section_heading_candidate")
            break
    for pattern in cfg["row_quality_rules"]["test_placeholder_patterns"]:
        if re.search(pattern, text, flags=re.I):
            reasons.append("test_placeholder_candidate")
            break
    if not normalize(jurisdiction):
        reasons.append("missing_jurisdiction")
    return reasons


def load_conflict_report(cfg: dict) -> dict:
    p = ROOT / cfg["conflict_report"]
    if not p.is_file():
        raise FileNotFoundError(p)
    data = json.loads(p.read_text(encoding="utf-8"))
    if data.get("status") != "PASS":
        raise RuntimeError("conflict evidence report is not PASS")
    return data


def registry_counts(cfg: dict) -> dict:
    p = ROOT / cfg["registry_db"]
    if not p.is_file():
        raise FileNotFoundError(p)
    with sqlite3.connect(f"file:{p.resolve()}?mode=ro", uri=True) as c:
        return {
            "buckets": int(c.execute(
                "SELECT COUNT(*) FROM identity_evidence_buckets"
            ).fetchone()[0]),
            "evidence_rows": int(c.execute(
                "SELECT COUNT(*) FROM identity_bucket_evidence"
            ).fetchone()[0]),
            "alias_rows": int(c.execute(
                "SELECT COUNT(*) FROM identity_alias_evidence"
            ).fetchone()[0]),
        }


def plan(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = load_config(config_path)
    ref = load_reference(cfg)
    conflicts = load_conflict_report(cfg)
    counts = registry_counts(cfg)
    failures = []

    if counts["buckets"] != 1650:
        failures.append("unexpected_registry_bucket_count")
    if counts["evidence_rows"] != 8111:
        failures.append("unexpected_registry_evidence_count")
    if conflicts["conflict_groups"] != 28:
        failures.append("unexpected_conflict_group_count")
    if conflicts["conflict_buckets"] != 57:
        failures.append("unexpected_conflict_bucket_count")

    pair_classes = Counter()
    pair_count = 0
    for group in conflicts["groups"]:
        for pair in group["pairs"]:
            pair_count += 1
            pair_classes[classify_pair(pair, ref)["review_class"]] += 1

    quality = []
    accounted_missing_evidence_rows = 0
    for item in conflicts["missing_jurisdiction"]:
        reasons = row_quality_reason(
            item["display_legal_name"], None, cfg
        )
        evidence_count = len(item.get("evidence") or [])
        accounted_missing_evidence_rows += evidence_count
        quality.append({
            "identity_bucket_id": item["identity_bucket_id"],
            "registrant_ticker": item["registrant_ticker"],
            "display_legal_name": item["display_legal_name"],
            "evidence_rows": evidence_count,
            "reasons": reasons,
            "automatic_exclusion": False,
        })

    return {
        "status":"FAIL" if failures else "PASS",
        "failures":failures,
        "registry":counts,
        "conflicts":{
            "groups":int(conflicts["conflict_groups"]),
            "buckets":int(conflicts["conflict_buckets"]),
            "pairs":pair_count,
            "evidence_rows":int(conflicts["conflict_evidence_rows"]),
            "pair_review_classes":dict(sorted(pair_classes.items())),
        },
        "row_quality_candidates":{
            "buckets":len(quality),
            "evidence_rows":accounted_missing_evidence_rows,
            "candidates":quality,
        },
        "jurisdiction_reference":{
            "equivalence_candidate_concepts":
                len(ref["equivalence_candidates"]),
            "hierarchy_roots":len(ref["hierarchy_candidates"]),
            "authoritative_global_reference":False,
            "writeback_allowed":False,
        },
        "identity_contract":{
            "automatic_merge":False,
            "automatic_split":False,
            "canonical_entities_created":False,
            "identity_verdicts_written":False,
            "row_exclusions_written":False,
        },
        "main_db_mutated":False,
        "graph_edges_written":False,
        "next_gate":cfg["next_gate"],
    }


def ensure_schema(c: sqlite3.Connection):
    c.executescript("""
    CREATE TABLE foundation_runs(
      foundation_run_id TEXT PRIMARY KEY,
      version TEXT NOT NULL,
      status TEXT NOT NULL,
      config_json TEXT NOT NULL,
      config_sha256 TEXT NOT NULL,
      started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      finished_at TEXT,
      conflict_groups INTEGER NOT NULL DEFAULT 0,
      conflict_pairs INTEGER NOT NULL DEFAULT 0,
      row_quality_candidates INTEGER NOT NULL DEFAULT 0
    );

    CREATE TABLE jurisdiction_reference_candidates(
      reference_candidate_id TEXT PRIMARY KEY,
      concept_id TEXT NOT NULL,
      alias_normalized TEXT NOT NULL,
      alias_raw TEXT NOT NULL,
      candidate_kind TEXT NOT NULL,
      context_required INTEGER NOT NULL,
      authoritative INTEGER NOT NULL DEFAULT 0,
      writeback_allowed INTEGER NOT NULL DEFAULT 0,
      UNIQUE(concept_id,alias_normalized)
    );

    CREATE TABLE jurisdiction_hierarchy_candidates(
      hierarchy_candidate_id TEXT PRIMARY KEY,
      parent_normalized TEXT NOT NULL,
      child_normalized TEXT NOT NULL,
      relation_kind TEXT NOT NULL,
      authoritative INTEGER NOT NULL DEFAULT 0,
      writeback_allowed INTEGER NOT NULL DEFAULT 0,
      UNIQUE(parent_normalized,child_normalized)
    );

    CREATE TABLE identity_conflict_group_reviews(
      conflict_group_id TEXT PRIMARY KEY,
      foundation_run_id TEXT NOT NULL,
      registrant_ticker TEXT NOT NULL,
      normalized_legal_name TEXT NOT NULL,
      bucket_count INTEGER NOT NULL,
      pair_count INTEGER NOT NULL,
      identity_verdict TEXT,
      automatic_decision INTEGER NOT NULL DEFAULT 0,
      metadata_json TEXT
    );

    CREATE TABLE identity_conflict_pair_reviews(
      pair_review_id TEXT PRIMARY KEY,
      conflict_group_id TEXT NOT NULL,
      left_jurisdiction TEXT NOT NULL,
      right_jurisdiction TEXT NOT NULL,
      review_class TEXT NOT NULL,
      reference_relation_json TEXT NOT NULL,
      shared_accession_count INTEGER NOT NULL,
      shared_accessions_json TEXT NOT NULL,
      temporal_relation TEXT NOT NULL,
      dba_cross_match_count INTEGER NOT NULL,
      identity_verdict TEXT,
      automatic_merge INTEGER NOT NULL DEFAULT 0,
      automatic_split INTEGER NOT NULL DEFAULT 0,
      metadata_json TEXT
    );

    CREATE TABLE row_quality_candidates(
      row_quality_candidate_id TEXT PRIMARY KEY,
      identity_bucket_id TEXT NOT NULL,
      registrant_ticker TEXT NOT NULL,
      display_legal_name TEXT NOT NULL,
      reason_json TEXT NOT NULL,
      evidence_row_count INTEGER NOT NULL,
      evidence_json TEXT NOT NULL,
      review_status TEXT NOT NULL DEFAULT 'pending',
      automatic_exclusion INTEGER NOT NULL DEFAULT 0,
      exclusion_applied INTEGER NOT NULL DEFAULT 0
    );

    CREATE INDEX idx_pair_review_class
      ON identity_conflict_pair_reviews(review_class);
    CREATE INDEX idx_row_quality_status
      ON row_quality_candidates(review_status);
    """)


def build(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = load_config(config_path)
    pre = plan(config_path)
    if pre["status"] != "PASS":
        raise RuntimeError(pre["failures"])
    ref = load_reference(cfg)
    conflicts = load_conflict_report(cfg)

    out_db = ROOT / cfg["output_db"]
    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()

    cfg_json = json.dumps(cfg, sort_keys=True)
    cfg_sha = hashlib.sha256(cfg_json.encode("utf-8")).hexdigest()
    run_id = stable_id("idresfoundation",cfg["version"],cfg_sha)

    pair_counts = Counter()
    group_count = 0
    pair_count = 0
    quality_count = 0

    with sqlite3.connect(out_db) as c:
        ensure_schema(c)
        c.execute(
            """INSERT INTO foundation_runs(
               foundation_run_id,version,status,config_json,config_sha256
               ) VALUES (?,?,?,?,?)""",
            (run_id,cfg["version"],"running",cfg_json,cfg_sha),
        )

        for rec in ref["equivalence_candidates"]:
            for alias in rec["aliases"]:
                c.execute(
                    """INSERT INTO jurisdiction_reference_candidates(
                       reference_candidate_id,concept_id,alias_normalized,
                       alias_raw,candidate_kind,context_required,
                       authoritative,writeback_allowed
                       ) VALUES (?,?,?,?,?,?,0,0)""",
                    (
                        stable_id("jurref",rec["concept_id"],normalize(alias)),
                        rec["concept_id"],normalize(alias),alias,
                        rec["candidate_kind"],int(bool(rec.get("context_required"))),
                    ),
                )

        for parent, rec in ref["hierarchy_candidates"].items():
            for child in rec["children"]:
                c.execute(
                    """INSERT INTO jurisdiction_hierarchy_candidates(
                       hierarchy_candidate_id,parent_normalized,
                       child_normalized,relation_kind,
                       authoritative,writeback_allowed
                       ) VALUES (?,?,?,'candidate_parent_jurisdiction',0,0)""",
                    (
                        stable_id("jurhier",normalize(parent),normalize(child)),
                        normalize(parent),normalize(child),
                    ),
                )

        for g in conflicts["groups"]:
            group_count += 1
            c.execute(
                """INSERT INTO identity_conflict_group_reviews(
                   conflict_group_id,foundation_run_id,registrant_ticker,
                   normalized_legal_name,bucket_count,pair_count,
                   identity_verdict,automatic_decision,metadata_json
                   ) VALUES (?,?,?,?,?,?,NULL,0,?)""",
                (
                    g["conflict_group_id"],run_id,g["registrant_ticker"],
                    g["normalized_legal_name"],len(g["buckets"]),len(g["pairs"]),
                    json.dumps({
                        "canonical_entity_created":False,
                        "source_automatic_decision":g.get("automatic_decision"),
                    },sort_keys=True),
                ),
            )
            for p in g["pairs"]:
                review = classify_pair(p,ref)
                pair_count += 1
                pair_counts[review["review_class"]] += 1
                pair_id = stable_id(
                    "idpairreview",
                    g["conflict_group_id"],
                    p["left_jurisdiction"],
                    p["right_jurisdiction"],
                )
                c.execute(
                    """INSERT INTO identity_conflict_pair_reviews(
                       pair_review_id,conflict_group_id,
                       left_jurisdiction,right_jurisdiction,
                       review_class,reference_relation_json,
                       shared_accession_count,shared_accessions_json,
                       temporal_relation,dba_cross_match_count,
                       identity_verdict,automatic_merge,automatic_split,
                       metadata_json
                       ) VALUES (?,?,?,?,?,?,?,?,?,?,NULL,0,0,?)""",
                    (
                        pair_id,g["conflict_group_id"],
                        p["left_jurisdiction"],p["right_jurisdiction"],
                        review["review_class"],
                        json.dumps(review["reference_relation"],sort_keys=True),
                        review["shared_accession_count"],
                        json.dumps(p.get("shared_accessions") or []),
                        review["temporal_relation"],
                        review["dba_cross_match_count"],
                        json.dumps({
                            "registrant_ticker":g["registrant_ticker"],
                            "normalized_legal_name":g["normalized_legal_name"],
                            "identity_verdict_written":False,
                        },sort_keys=True),
                    ),
                )

        for item in conflicts["missing_jurisdiction"]:
            reasons = row_quality_reason(item["display_legal_name"],None,cfg)
            quality_count += 1
            qid = stable_id(
                "rowquality",
                item["identity_bucket_id"],
                ",".join(sorted(reasons)),
            )
            c.execute(
                """INSERT INTO row_quality_candidates(
                   row_quality_candidate_id,identity_bucket_id,
                   registrant_ticker,display_legal_name,reason_json,
                   evidence_row_count,evidence_json,review_status,
                   automatic_exclusion,exclusion_applied
                   ) VALUES (?,?,?,?,?,?,?,'pending',0,0)""",
                (
                    qid,item["identity_bucket_id"],item["registrant_ticker"],
                    item["display_legal_name"],json.dumps(reasons),
                    len(item.get("evidence") or []),
                    json.dumps(item.get("evidence") or [],ensure_ascii=False),
                ),
            )

        c.execute(
            """UPDATE foundation_runs
               SET status='completed',finished_at=CURRENT_TIMESTAMP,
                   conflict_groups=?,conflict_pairs=?,row_quality_candidates=?
               WHERE foundation_run_id=?""",
            (group_count,pair_count,quality_count,run_id),
        )
        c.commit()

    # Complete QA report: all 30 pairs + all quality candidates.
    report_dir = ROOT / cfg["report_dir"]
    report_dir.mkdir(parents=True,exist_ok=True)
    with sqlite3.connect(out_db) as c:
        pair_rows = c.execute(
            """SELECT g.registrant_ticker,g.normalized_legal_name,
                      p.left_jurisdiction,p.right_jurisdiction,p.review_class,
                      p.reference_relation_json,p.shared_accession_count,
                      p.shared_accessions_json,p.temporal_relation,
                      p.dba_cross_match_count,p.identity_verdict
               FROM identity_conflict_pair_reviews p
               JOIN identity_conflict_group_reviews g
                 ON g.conflict_group_id=p.conflict_group_id
               ORDER BY g.registrant_ticker,g.normalized_legal_name,
                        p.left_jurisdiction,p.right_jurisdiction"""
        ).fetchall()
        qrows = c.execute(
            """SELECT registrant_ticker,display_legal_name,reason_json,
                      evidence_row_count,evidence_json,review_status,
                      automatic_exclusion,exclusion_applied
               FROM row_quality_candidates
               ORDER BY registrant_ticker,display_legal_name"""
        ).fetchall()

    pairs_payload = []
    for r in pair_rows:
        pairs_payload.append({
            "registrant_ticker":r[0],
            "normalized_legal_name":r[1],
            "left_jurisdiction":r[2],
            "right_jurisdiction":r[3],
            "review_class":r[4],
            "reference_relation":json.loads(r[5]),
            "shared_accession_count":r[6],
            "shared_accessions":json.loads(r[7]),
            "temporal_relation":r[8],
            "dba_cross_match_count":r[9],
            "identity_verdict":r[10],
        })
    quality_payload = []
    for r in qrows:
        quality_payload.append({
            "registrant_ticker":r[0],
            "display_legal_name":r[1],
            "reasons":json.loads(r[2]),
            "evidence_row_count":r[3],
            "evidence":json.loads(r[4]),
            "review_status":r[5],
            "automatic_exclusion":bool(r[6]),
            "exclusion_applied":bool(r[7]),
        })

    qa_path = report_dir / "qa_all_pairs_and_quality_candidates.json"
    qa_path.write_text(
        json.dumps({
            "status":"PASS",
            "pair_count":len(pairs_payload),
            "row_quality_candidate_count":len(quality_payload),
            "pairs":pairs_payload,
            "row_quality_candidates":quality_payload,
            "canonical_entities_created":False,
            "identity_verdicts_written":False,
            "row_exclusions_written":False,
            "graph_edges_written":False,
        },indent=2,ensure_ascii=False)+"\n",
        encoding="utf-8",
    )

    return {
        "status":"PASS",
        "foundation_run_id":run_id,
        "conflict_groups_written":group_count,
        "conflict_pairs_written":pair_count,
        "pair_review_classes":dict(sorted(pair_counts.items())),
        "row_quality_candidates_written":quality_count,
        "qa_report":str(qa_path),
        "canonical_entities_created":False,
        "identity_verdicts_written":False,
        "row_exclusions_written":False,
        "main_db_mutated":False,
        "graph_edges_written":False,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    p.add_argument("--stage",required=True,choices=("plan","build"))
    a=p.parse_args()
    r=plan(a.config) if a.stage=="plan" else build(a.config)
    print(json.dumps(r,indent=2))


if __name__=="__main__":
    main()

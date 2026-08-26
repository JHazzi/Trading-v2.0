from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from dataclasses import dataclass
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = (
    ROOT / "config/event_graph_entity_identity_audit_v001.json"
)


def normalize_basic(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = (
        text.replace("\u00a0", " ")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
    )
    text = text.casefold()
    text = re.sub(r"[.,;:()\[\]{}]", " ", text)
    text = re.sub(r"[-_/]+", " ", text)
    text = re.sub(r"\s+", " ", text).strip()
    return text


def token_key(value: str) -> str:
    return " ".join(normalize_basic(value).split())


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join(str(x) for x in parts).encode("utf-8")
    return prefix + "_" + hashlib.sha256(raw).hexdigest()


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg["version"] != "event_graph_entity_identity_audit_v001":
        raise ValueError("unexpected identity audit version")
    if cfg["candidate_rules"]["automatic_merge_allowed"]:
        raise ValueError("automatic merge must remain disabled")
    if cfg["candidate_rules"]["fuzzy_similarity"]:
        raise ValueError("fuzzy identity matching is not allowed in V001")
    return cfg


def form_alias_map(cfg: dict) -> dict[tuple[str, ...], str]:
    out: dict[tuple[str, ...], str] = {}
    for family, aliases in cfg["legal_form_families"].items():
        for alias in aliases:
            toks = tuple(token_key(alias).split())
            if not toks:
                continue
            if toks in out and out[toks] != family:
                raise ValueError(f"legal form alias collision: {toks}")
            out[toks] = family
    return out


def legal_form_signature(name: str, cfg: dict) -> tuple[str, str | None]:
    """
    Canonicalize the legal-form rendering while preserving the form family.

    Examples:
      Acme, Inc.       -> ("acme", "INC")
      Acme Incorporated -> ("acme", "INC")

    We do not collapse INC and LLC, and we do not use the stem alone as
    identity evidence.
    """
    toks = token_key(name).split()
    aliases = form_alias_map(cfg)

    best = None
    for alias_tokens, family in aliases.items():
        n = len(alias_tokens)
        if n <= len(toks) and tuple(toks[-n:]) == alias_tokens:
            if best is None or n > best[0]:
                best = (n, family)

    if best is None:
        return (" ".join(toks), None)
    n, family = best
    stem = " ".join(toks[:-n]).strip()
    return (stem, family)


def canonical_identity_key(name: str, cfg: dict) -> str:
    stem, family = legal_form_signature(name, cfg)
    if family is None:
        return f"NOFORM::{stem}"
    return f"{family}::{stem}"


@dataclass(frozen=True)
class NameProfile:
    registry_name_id: str
    normalized_name: str
    display_name: str
    occurrence_count: int
    first_at: str
    last_at: str
    registrants: frozenset[int]
    accessions: frozenset[str]


def load_profiles(conn: sqlite3.Connection) -> list[NameProfile]:
    names = conn.execute(
        """
        SELECT
          registry_name_id,normalized_name,display_name,
          evidence_occurrence_count,first_evidence_available_at,
          last_evidence_available_at
        FROM registry_name_records
        ORDER BY normalized_name,registry_name_id
        """
    ).fetchall()

    ev = conn.execute(
        """
        SELECT registry_name_id,registrant_entity_id,accession_number
        FROM registry_name_evidence
        ORDER BY registry_name_id,evidence_available_at,evidence_claim_id
        """
    ).fetchall()
    registrants = defaultdict(set)
    accessions = defaultdict(set)
    for name_id, reg_id, accession in ev:
        registrants[str(name_id)].add(int(reg_id))
        accessions[str(name_id)].add(str(accession))

    out = []
    for row in names:
        out.append(
            NameProfile(
                registry_name_id=str(row[0]),
                normalized_name=str(row[1]),
                display_name=str(row[2]),
                occurrence_count=int(row[3]),
                first_at=str(row[4]),
                last_at=str(row[5]),
                registrants=frozenset(registrants[str(row[0])]),
                accessions=frozenset(accessions[str(row[0])]),
            )
        )
    return out


def plan(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = load_config(config_path)
    db = ROOT / cfg["registry_db"]
    if not db.is_file():
        raise FileNotFoundError(db)

    with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as conn:
        profiles = load_profiles(conn)
        evidence_rows = conn.execute(
            "SELECT COUNT(*) FROM registry_name_evidence"
        ).fetchone()[0]
        rejections = conn.execute(
            "SELECT COUNT(*) FROM registry_rejections"
        ).fetchone()[0]

    failures = []
    if not profiles:
        failures.append("empty_registry")
    if evidence_rows == 0:
        failures.append("empty_registry_evidence")

    by_occurrence = Counter(
        (
            "1"
            if p.occurrence_count == 1
            else "2"
            if p.occurrence_count == 2
            else "3-5"
            if p.occurrence_count <= 5
            else "6-10"
            if p.occurrence_count <= 10
            else ">10"
        )
        for p in profiles
    )

    singleton_registrant = sum(len(p.registrants) == 1 for p in profiles)
    multi_registrant = sum(len(p.registrants) > 1 for p in profiles)

    keys = defaultdict(list)
    for p in profiles:
        keys[canonical_identity_key(p.display_name, cfg)].append(p)
    collision_keys = {
        k: v for k, v in keys.items() if len(v) > 1
    }
    shared_registrant_collision_keys = 0
    same_accession_conflict_pairs = 0
    potential_pairs = 0
    for group in collision_keys.values():
        for i in range(len(group)):
            for j in range(i + 1, len(group)):
                a, b = group[i], group[j]
                if not (a.registrants & b.registrants):
                    continue
                shared_registrant_collision_keys += 1
                potential_pairs += 1
                if a.accessions & b.accessions:
                    same_accession_conflict_pairs += 1

    return {
        "status": "FAIL" if failures else "PASS",
        "failures": failures,
        "registry_name_records": len(profiles),
        "registry_evidence_rows": int(evidence_rows),
        "registry_rejections": int(rejections),
        "occurrence_distribution": dict(sorted(by_occurrence.items())),
        "names_with_single_registrant": singleton_registrant,
        "names_with_multiple_registrants": multi_registrant,
        "identity_key_collision_groups": len(collision_keys),
        "shared_registrant_candidate_pairs": potential_pairs,
        "same_accession_conflict_pairs": same_accession_conflict_pairs,
        "candidate_rules": cfg["candidate_rules"],
        "main_db_mutated": False,
        "canonical_entities_created": False,
        "graph_edges_written": False,
        "next_gate": (
            "If PASS, build a separate candidate-pair DB. Candidate pairs are "
            "identity hypotheses only; auto-merge remains forbidden."
        ),
    }


def ensure_schema(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE identity_runs(
      identity_run_id TEXT PRIMARY KEY,
      version TEXT NOT NULL,
      status TEXT NOT NULL,
      config_json TEXT NOT NULL,
      config_sha256 TEXT NOT NULL,
      started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      finished_at TEXT,
      profiles_written INTEGER NOT NULL DEFAULT 0,
      candidate_pairs_written INTEGER NOT NULL DEFAULT 0,
      error_json TEXT
    );

    CREATE TABLE identity_name_profiles(
      registry_name_id TEXT PRIMARY KEY,
      display_name TEXT NOT NULL,
      normalized_name TEXT NOT NULL,
      canonical_identity_key TEXT NOT NULL,
      legal_form_family TEXT,
      legal_name_stem TEXT NOT NULL,
      occurrence_count INTEGER NOT NULL,
      first_evidence_available_at TEXT NOT NULL,
      last_evidence_available_at TEXT NOT NULL,
      registrant_ids_json TEXT NOT NULL,
      accession_count INTEGER NOT NULL,
      metadata_json TEXT
    );

    CREATE TABLE identity_candidate_pairs(
      identity_candidate_id TEXT PRIMARY KEY,
      identity_run_id TEXT NOT NULL,
      left_registry_name_id TEXT NOT NULL,
      right_registry_name_id TEXT NOT NULL,
      left_display_name TEXT NOT NULL,
      right_display_name TEXT NOT NULL,
      candidate_kind TEXT NOT NULL,
      canonical_identity_key TEXT NOT NULL,
      shared_registrant_ids_json TEXT NOT NULL,
      shared_registrant_count INTEGER NOT NULL,
      same_accession_cooccurrence INTEGER NOT NULL,
      shared_accession_count INTEGER NOT NULL,
      temporal_relation TEXT NOT NULL,
      auto_merge_allowed INTEGER NOT NULL DEFAULT 0,
      review_status TEXT NOT NULL DEFAULT 'pending',
      metadata_json TEXT,
      UNIQUE(identity_run_id,left_registry_name_id,right_registry_name_id)
    );

    CREATE TABLE qa_samples(
      qa_sample_id TEXT PRIMARY KEY,
      identity_candidate_id TEXT NOT NULL,
      sample_group TEXT NOT NULL,
      deterministic_rank TEXT NOT NULL,
      manual_label TEXT,
      manual_notes TEXT,
      UNIQUE(identity_candidate_id,sample_group)
    );

    CREATE INDEX idx_identity_key
      ON identity_name_profiles(canonical_identity_key);
    CREATE INDEX idx_identity_candidate_kind
      ON identity_candidate_pairs(candidate_kind,review_status);
    """)


def temporal_relation(a: NameProfile, b: NameProfile) -> str:
    if a.last_at < b.first_at:
        return "left_before_right_no_overlap"
    if b.last_at < a.first_at:
        return "right_before_left_no_overlap"
    return "evidence_ranges_overlap"


def candidate_kind(a: NameProfile, b: NameProfile, cfg: dict) -> str:
    if token_key(a.display_name) == token_key(b.display_name):
        return "punctuation_spacing_variant"
    sa, fa = legal_form_signature(a.display_name, cfg)
    sb, fb = legal_form_signature(b.display_name, cfg)
    if sa == sb and fa == fb and fa is not None:
        return "equivalent_legal_form_rendering"
    return "identity_key_collision"


def build(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = load_config(config_path)
    pre = plan(config_path)
    if pre["status"] != "PASS":
        raise RuntimeError(pre["failures"])

    src_db = ROOT / cfg["registry_db"]
    out_db = ROOT / cfg["output_db"]
    cfg_json = json.dumps(cfg, sort_keys=True)
    cfg_sha = hashlib.sha256(cfg_json.encode("utf-8")).hexdigest()
    run_id = stable_id("identityrun", cfg["version"], cfg_sha)

    with sqlite3.connect(f"file:{src_db.resolve()}?mode=ro", uri=True) as src:
        profiles = load_profiles(src)

    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()

    groups = defaultdict(list)
    for p in profiles:
        groups[canonical_identity_key(p.display_name, cfg)].append(p)

    pair_count = 0
    conflict_count = 0
    by_kind = Counter()

    with sqlite3.connect(out_db) as out:
        ensure_schema(out)
        out.execute(
            """
            INSERT INTO identity_runs(
              identity_run_id,version,status,config_json,config_sha256
            ) VALUES (?,?,?,?,?)
            """,
            (run_id, cfg["version"], "running", cfg_json, cfg_sha),
        )

        for p in profiles:
            stem, family = legal_form_signature(p.display_name, cfg)
            out.execute(
                """
                INSERT INTO identity_name_profiles(
                  registry_name_id,display_name,normalized_name,
                  canonical_identity_key,legal_form_family,legal_name_stem,
                  occurrence_count,first_evidence_available_at,
                  last_evidence_available_at,registrant_ids_json,
                  accession_count,metadata_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    p.registry_name_id,p.display_name,p.normalized_name,
                    canonical_identity_key(p.display_name,cfg),
                    family,stem,p.occurrence_count,p.first_at,p.last_at,
                    json.dumps(sorted(p.registrants)),
                    len(p.accessions),
                    json.dumps({
                        "canonical_entity_created": False,
                        "identity_merged": False,
                    }, sort_keys=True),
                ),
            )

        for key, group in sorted(groups.items()):
            if len(group) < 2:
                continue
            ordered = sorted(group, key=lambda x: x.registry_name_id)
            for i in range(len(ordered)):
                for j in range(i + 1, len(ordered)):
                    a, b = ordered[i], ordered[j]
                    shared_regs = sorted(a.registrants & b.registrants)
                    if cfg["candidate_rules"]["require_shared_registrant"] and not shared_regs:
                        continue
                    shared_accessions = sorted(a.accessions & b.accessions)
                    same_accession = int(bool(shared_accessions))
                    if same_accession:
                        conflict_count += 1
                    kind = candidate_kind(a,b,cfg)
                    cid = stable_id(
                        "idcand",run_id,a.registry_name_id,b.registry_name_id
                    )
                    out.execute(
                        """
                        INSERT INTO identity_candidate_pairs(
                          identity_candidate_id,identity_run_id,
                          left_registry_name_id,right_registry_name_id,
                          left_display_name,right_display_name,
                          candidate_kind,canonical_identity_key,
                          shared_registrant_ids_json,shared_registrant_count,
                          same_accession_cooccurrence,shared_accession_count,
                          temporal_relation,auto_merge_allowed,
                          review_status,metadata_json
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            cid,run_id,a.registry_name_id,b.registry_name_id,
                            a.display_name,b.display_name,kind,key,
                            json.dumps(shared_regs),len(shared_regs),
                            same_accession,len(shared_accessions),
                            temporal_relation(a,b),0,"pending",
                            json.dumps({
                                "same_accession_conflict":
                                    bool(same_accession),
                                "merge_performed": False,
                            }, sort_keys=True),
                        ),
                    )
                    pair_count += 1
                    by_kind[kind] += 1

        out.execute(
            """
            UPDATE identity_runs
            SET status='completed',finished_at=CURRENT_TIMESTAMP,
                profiles_written=?,candidate_pairs_written=?
            WHERE identity_run_id=?
            """,
            (len(profiles),pair_count,run_id),
        )
        out.commit()

    return {
        "status":"PASS",
        "identity_run_id":run_id,
        "profiles_written":len(profiles),
        "candidate_pairs_written":pair_count,
        "same_accession_conflict_pairs":conflict_count,
        "by_candidate_kind":dict(sorted(by_kind.items())),
        "automatic_merge_allowed":False,
        "canonical_entities_created":False,
        "main_db_mutated":False,
        "graph_edges_written":False,
    }


def qa_sample(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg=load_config(config_path)
    db=ROOT/cfg["output_db"]
    limit=int(cfg["qa"]["sample_pairs_per_candidate_kind"])
    with sqlite3.connect(db) as conn:
        conn.execute("DELETE FROM qa_samples")
        rows=conn.execute(
            """
            SELECT identity_candidate_id,candidate_kind
            FROM identity_candidate_pairs
            ORDER BY identity_candidate_id
            """
        ).fetchall()
        groups=defaultdict(list)
        for cid,kind in rows:
            groups[str(kind)].append(str(cid))
        for group,ids in groups.items():
            ranked=sorted(
                ids,
                key=lambda x:hashlib.sha256(
                    f"{group}\0{x}".encode("utf-8")
                ).hexdigest()
            )[:limit]
            for rank,cid in enumerate(ranked):
                conn.execute(
                    """
                    INSERT INTO qa_samples(
                      qa_sample_id,identity_candidate_id,
                      sample_group,deterministic_rank
                    ) VALUES (?,?,?,?)
                    """,
                    (
                        stable_id("idqas",group,cid),cid,group,
                        f"{rank:06d}",
                    ),
                )
        conn.commit()
        sample=conn.execute(
            """
            SELECT
              q.sample_group,q.deterministic_rank,
              c.identity_candidate_id,c.left_display_name,
              c.right_display_name,c.candidate_kind,
              c.shared_registrant_count,
              c.same_accession_cooccurrence,
              c.shared_accession_count,c.temporal_relation,
              c.canonical_identity_key
            FROM qa_samples q
            JOIN identity_candidate_pairs c
              ON c.identity_candidate_id=q.identity_candidate_id
            ORDER BY q.sample_group,q.deterministic_rank
            """
        ).fetchall()

    names=[
        "sample_group","deterministic_rank","identity_candidate_id",
        "left_display_name","right_display_name","candidate_kind",
        "shared_registrant_count","same_accession_cooccurrence",
        "shared_accession_count","temporal_relation",
        "canonical_identity_key",
    ]
    payload=[dict(zip(names,r)) for r in sample]
    report_dir=ROOT/cfg["report_dir"]
    report_dir.mkdir(parents=True,exist_ok=True)
    path=report_dir/"qa_sample.json"
    path.write_text(json.dumps(payload,indent=2)+"\n",encoding="utf-8")
    return {
        "status":"PASS",
        "sample_rows":len(payload),
        "sample_file":str(path),
        "automatic_merge_allowed":False,
        "manual_review_required":True,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    p.add_argument("--stage",required=True,choices=("plan","build","qa-sample"))
    a=p.parse_args()
    if a.stage=="plan":
        r=plan(a.config)
    elif a.stage=="build":
        r=build(a.config)
    else:
        r=qa_sample(a.config)
    print(json.dumps(r,indent=2))


if __name__=="__main__":
    main()

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
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/event_graph_entity_registry_v002.json"


def normalize_text(value: str | None) -> str:
    if value is None:
        return ""
    text = unicodedata.normalize("NFKC", str(value))
    text = (
        text.replace("\u00a0", " ")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
    )
    text = re.sub(r"\s+", " ", text).strip()
    text = text.strip(" \t\r\n,;:.")
    return text.casefold()


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join(str(x) for x in parts).encode("utf-8")
    return prefix + "_" + hashlib.sha256(raw).hexdigest()


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg["version"] != "event_graph_entity_registry_v002":
        raise ValueError("unexpected registry version")
    guards = cfg["hard_guards"]
    if not all(bool(v) for v in guards.values()):
        raise ValueError("all V002 hard guards must remain enabled")
    if cfg["normalization"]["fuzzy_matching"]:
        raise ValueError("fuzzy matching must remain disabled")
    if cfg["normalization"]["jurisdiction_mapping"]:
        raise ValueError("jurisdiction mapping must remain disabled")
    return cfg


@dataclass(frozen=True)
class Observation:
    structured_row_id: str
    corpus_document_id: str
    accession_number: str
    registrant_asset_id: int
    registrant_entity_id: int
    registrant_ticker: str
    evidence_available_at: str
    legal_name_raw: str
    legal_name_clean: str
    jurisdiction_raw: str | None
    location_raw: str | None
    dba_alias_raw: str | None
    ownership_raw: str | None
    ownership_percent: float | None
    footnote_refs_json: str
    table_index: int
    source_row_index: int
    schema_family: str
    raw_sha256: str
    source_url: str | None


def load_observations(conn: sqlite3.Connection) -> list[Observation]:
    rows = conn.execute(
        """
        SELECT
          structured_row_id,corpus_document_id,accession_number,
          registrant_asset_id,registrant_entity_id,registrant_ticker,
          evidence_available_at,legal_name_raw,legal_name_clean,
          jurisdiction_raw,location_raw,dba_alias_raw,
          ownership_raw,ownership_percent,legal_name_footnote_refs_json,
          table_index,source_row_index,schema_family,raw_sha256,source_url
        FROM structured_ex21_rows
        ORDER BY
          evidence_available_at,accession_number,table_index,
          source_row_index,structured_row_id
        """
    ).fetchall()
    return [Observation(*r) for r in rows]


def bucket_key(o: Observation) -> tuple[int, str, str]:
    name = normalize_text(o.legal_name_clean)
    jurisdiction = normalize_text(o.jurisdiction_raw) or "__MISSING__"
    return (int(o.registrant_entity_id), name, jurisdiction)


def plan(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = load_config(config_path)
    db = ROOT / cfg["structured_rows_db"]
    if not db.is_file():
        raise FileNotFoundError(db)

    with sqlite3.connect(f"file:{db.resolve()}?mode=ro", uri=True) as conn:
        obs = load_observations(conn)

    failures = []
    if not obs:
        failures.append("zero_structured_rows")
    if any(not normalize_text(o.legal_name_clean) for o in obs):
        failures.append("empty_normalized_legal_name")

    buckets = defaultdict(list)
    name_to_juris = defaultdict(set)
    name_to_regs = defaultdict(set)
    legal_names_by_reg = defaultdict(set)
    dba_rows = 0
    ownership_rows = 0
    footnote_rows = 0
    missing_jurisdiction_rows = 0

    for o in obs:
        key = bucket_key(o)
        buckets[key].append(o)
        n = normalize_text(o.legal_name_clean)
        j = normalize_text(o.jurisdiction_raw) or "__MISSING__"
        name_to_juris[n].add(j)
        name_to_regs[n].add(int(o.registrant_entity_id))
        legal_names_by_reg[int(o.registrant_entity_id)].add(n)
        if o.dba_alias_raw:
            dba_rows += 1
        if o.ownership_raw:
            ownership_rows += 1
        if json.loads(o.footnote_refs_json):
            footnote_rows += 1
        if not normalize_text(o.jurisdiction_raw):
            missing_jurisdiction_rows += 1

    same_name_multi_juri = {
        n: js for n, js in name_to_juris.items() if len(js) > 1
    }
    same_name_multi_reg = {
        n: rs for n, rs in name_to_regs.items() if len(rs) > 1
    }

    dba_collision_rows = 0
    dba_collision_examples = []
    for o in obs:
        if not o.dba_alias_raw:
            continue
        d = normalize_text(o.dba_alias_raw)
        if d and d in legal_names_by_reg[int(o.registrant_entity_id)]:
            dba_collision_rows += 1
            if len(dba_collision_examples) < 20:
                dba_collision_examples.append({
                    "registrant_ticker": o.registrant_ticker,
                    "legal_name": o.legal_name_clean,
                    "jurisdiction": o.jurisdiction_raw,
                    "dba_alias": o.dba_alias_raw,
                })

    ownership_variation_buckets = 0
    for items in buckets.values():
        vals = {
            float(x.ownership_percent)
            for x in items
            if x.ownership_percent is not None
        }
        if len(vals) > 1:
            ownership_variation_buckets += 1

    bucket_sizes = Counter()
    for items in buckets.values():
        n = len(items)
        label = (
            "1" if n == 1 else
            "2" if n == 2 else
            "3-5" if n <= 5 else
            "6-10" if n <= 10 else
            ">10"
        )
        bucket_sizes[label] += 1

    missing_jurisdiction_buckets = sum(
        1 for (_, _, j) in buckets if j == "__MISSING__"
    )

    return {
        "status": "FAIL" if failures else "PASS",
        "failures": failures,
        "input_structured_rows": len(obs),
        "registrants": len({o.registrant_entity_id for o in obs}),
        "tickers": sorted({o.registrant_ticker for o in obs}),
        "evidence_buckets": len(buckets),
        "unique_normalized_legal_names_global": len(name_to_juris),
        "bucket_size_distribution": dict(sorted(bucket_sizes.items())),
        "jurisdiction": {
            "rows_with_missing_jurisdiction": missing_jurisdiction_rows,
            "buckets_with_missing_jurisdiction": missing_jurisdiction_buckets,
            "same_name_multiple_jurisdictions": len(same_name_multi_juri),
            "same_name_multiple_jurisdictions_examples": [
                {"normalized_name": n, "jurisdictions": sorted(js)}
                for n, js in sorted(same_name_multi_juri.items())[:20]
            ],
        },
        "registrant_scope": {
            "same_name_multiple_registrants": len(same_name_multi_reg),
            "same_name_multiple_registrants_examples": [
                {"normalized_name": n, "registrant_entity_ids": sorted(rs)}
                for n, rs in sorted(same_name_multi_reg.items())[:20]
            ],
        },
        "alias_evidence": {
            "rows_with_dba": dba_rows,
            "dba_rows_matching_a_legal_name_within_same_registrant":
                dba_collision_rows,
            "examples": dba_collision_examples,
        },
        "ownership_evidence": {
            "rows_with_ownership": ownership_rows,
            "buckets_with_multiple_numeric_ownership_values":
                ownership_variation_buckets,
        },
        "footnote_evidence": {
            "rows_with_footnote_refs": footnote_rows,
        },
        "identity_contract": {
            "canonical_entities_created": False,
            "cross_registrant_merge": False,
            "cross_jurisdiction_merge": False,
            "fuzzy_matching": False,
            "dba_used_as_identity_key": False,
        },
        "main_db_mutated": False,
        "graph_edges_written": False,
        "next_gate": cfg["next_gate"],
    }


def ensure_schema(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE registry_runs(
      registry_run_id TEXT PRIMARY KEY,
      version TEXT NOT NULL,
      status TEXT NOT NULL,
      config_json TEXT NOT NULL,
      config_sha256 TEXT NOT NULL,
      started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      finished_at TEXT,
      source_rows INTEGER NOT NULL DEFAULT 0,
      buckets_written INTEGER NOT NULL DEFAULT 0,
      evidence_rows_written INTEGER NOT NULL DEFAULT 0,
      alias_evidence_rows_written INTEGER NOT NULL DEFAULT 0,
      error_json TEXT
    );

    CREATE TABLE identity_evidence_buckets(
      identity_bucket_id TEXT PRIMARY KEY,
      registry_run_id TEXT NOT NULL,
      registrant_entity_id INTEGER NOT NULL,
      registrant_asset_id INTEGER NOT NULL,
      registrant_ticker TEXT NOT NULL,
      normalized_legal_name TEXT NOT NULL,
      display_legal_name TEXT NOT NULL,
      normalized_jurisdiction TEXT NOT NULL,
      jurisdiction_status TEXT NOT NULL,
      display_jurisdiction TEXT,
      evidence_occurrence_count INTEGER NOT NULL,
      first_evidence_available_at TEXT NOT NULL,
      last_evidence_available_at TEXT NOT NULL,
      accession_count INTEGER NOT NULL,
      dba_evidence_count INTEGER NOT NULL,
      ownership_evidence_count INTEGER NOT NULL,
      footnote_evidence_count INTEGER NOT NULL,
      ownership_values_json TEXT NOT NULL,
      identity_status TEXT NOT NULL DEFAULT 'evidence_bucket_not_canonical',
      metadata_json TEXT,
      UNIQUE(
        registry_run_id,registrant_entity_id,
        normalized_legal_name,normalized_jurisdiction
      )
    );

    CREATE TABLE identity_bucket_evidence(
      identity_bucket_evidence_id TEXT PRIMARY KEY,
      identity_bucket_id TEXT NOT NULL,
      structured_row_id TEXT NOT NULL,
      accession_number TEXT NOT NULL,
      corpus_document_id TEXT NOT NULL,
      evidence_available_at TEXT NOT NULL,
      legal_name_raw TEXT NOT NULL,
      legal_name_clean TEXT NOT NULL,
      jurisdiction_raw TEXT,
      location_raw TEXT,
      ownership_raw TEXT,
      ownership_percent REAL,
      footnote_refs_json TEXT NOT NULL,
      schema_family TEXT NOT NULL,
      table_index INTEGER NOT NULL,
      source_row_index INTEGER NOT NULL,
      raw_sha256 TEXT NOT NULL,
      source_url TEXT,
      UNIQUE(identity_bucket_id,structured_row_id)
    );

    CREATE TABLE identity_alias_evidence(
      alias_evidence_id TEXT PRIMARY KEY,
      identity_bucket_id TEXT NOT NULL,
      structured_row_id TEXT NOT NULL,
      alias_type TEXT NOT NULL,
      alias_raw TEXT NOT NULL,
      alias_normalized TEXT NOT NULL,
      evidence_available_at TEXT NOT NULL,
      accession_number TEXT NOT NULL,
      evidence_role TEXT NOT NULL,
      auto_merge_allowed INTEGER NOT NULL DEFAULT 0,
      metadata_json TEXT,
      UNIQUE(identity_bucket_id,structured_row_id,alias_type,alias_normalized)
    );

    CREATE INDEX idx_registry_v2_name
      ON identity_evidence_buckets(normalized_legal_name);
    CREATE INDEX idx_registry_v2_jurisdiction
      ON identity_evidence_buckets(normalized_jurisdiction);
    CREATE INDEX idx_registry_v2_registrant
      ON identity_evidence_buckets(registrant_entity_id,normalized_legal_name);
    CREATE INDEX idx_registry_v2_alias
      ON identity_alias_evidence(alias_normalized);
    """)


def build(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = load_config(config_path)
    pre = plan(config_path)
    if pre["status"] != "PASS":
        raise RuntimeError(pre["failures"])

    src_db = ROOT / cfg["structured_rows_db"]
    out_db = ROOT / cfg["output_db"]
    cfg_json = json.dumps(cfg, sort_keys=True)
    cfg_sha = hashlib.sha256(cfg_json.encode("utf-8")).hexdigest()
    run_id = stable_id("ereg2run", cfg["version"], cfg_sha)

    with sqlite3.connect(f"file:{src_db.resolve()}?mode=ro", uri=True) as src:
        obs = load_observations(src)

    grouped = defaultdict(list)
    for o in obs:
        grouped[bucket_key(o)].append(o)

    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()

    alias_rows = 0
    evidence_rows = 0

    with sqlite3.connect(out_db) as out:
        ensure_schema(out)
        out.execute(
            """
            INSERT INTO registry_runs(
              registry_run_id,version,status,config_json,config_sha256,
              source_rows
            ) VALUES (?,?,?,?,?,?)
            """,
            (run_id,cfg["version"],"running",cfg_json,cfg_sha,len(obs)),
        )
        out.commit()

        try:
            for (reg_id,norm_name,norm_juri), items in sorted(grouped.items()):
                raw_name_counts = Counter(x.legal_name_clean for x in items)
                display_name = sorted(
                    raw_name_counts,
                    key=lambda x: (-raw_name_counts[x], x.casefold(), x)
                )[0]

                juri_values = [x.jurisdiction_raw for x in items if x.jurisdiction_raw]
                if juri_values:
                    juri_counts = Counter(juri_values)
                    display_juri = sorted(
                        juri_counts,
                        key=lambda x: (-juri_counts[x], x.casefold(), x)
                    )[0]
                    juri_status = "observed"
                else:
                    display_juri = None
                    juri_status = "missing"

                first = min(x.evidence_available_at for x in items)
                last = max(x.evidence_available_at for x in items)
                accessions = {x.accession_number for x in items}
                dba_count = sum(bool(x.dba_alias_raw) for x in items)
                ownership_count = sum(bool(x.ownership_raw) for x in items)
                footnote_count = sum(
                    bool(json.loads(x.footnote_refs_json)) for x in items
                )
                ownership_values = sorted({
                    float(x.ownership_percent)
                    for x in items
                    if x.ownership_percent is not None
                })
                bucket_id = stable_id(
                    "idbucket2",run_id,reg_id,norm_name,norm_juri
                )

                out.execute(
                    """
                    INSERT INTO identity_evidence_buckets(
                      identity_bucket_id,registry_run_id,
                      registrant_entity_id,registrant_asset_id,
                      registrant_ticker,normalized_legal_name,
                      display_legal_name,normalized_jurisdiction,
                      jurisdiction_status,display_jurisdiction,
                      evidence_occurrence_count,
                      first_evidence_available_at,last_evidence_available_at,
                      accession_count,dba_evidence_count,
                      ownership_evidence_count,footnote_evidence_count,
                      ownership_values_json,metadata_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        bucket_id,run_id,int(reg_id),
                        int(items[0].registrant_asset_id),
                        items[0].registrant_ticker,
                        norm_name,display_name,norm_juri,juri_status,
                        display_juri,len(items),first,last,len(accessions),
                        dba_count,ownership_count,footnote_count,
                        json.dumps(ownership_values),
                        json.dumps({
                            "canonical_entity_created": False,
                            "cross_registrant_merge": False,
                            "cross_jurisdiction_merge": False,
                            "identity_key_scope": "registrant",
                        },sort_keys=True),
                    ),
                )

                for x in items:
                    evid_id = stable_id(
                        "idbke2",bucket_id,x.structured_row_id
                    )
                    out.execute(
                        """
                        INSERT INTO identity_bucket_evidence(
                          identity_bucket_evidence_id,identity_bucket_id,
                          structured_row_id,accession_number,
                          corpus_document_id,evidence_available_at,
                          legal_name_raw,legal_name_clean,jurisdiction_raw,
                          location_raw,ownership_raw,ownership_percent,
                          footnote_refs_json,schema_family,table_index,
                          source_row_index,raw_sha256,source_url
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            evid_id,bucket_id,x.structured_row_id,
                            x.accession_number,x.corpus_document_id,
                            x.evidence_available_at,x.legal_name_raw,
                            x.legal_name_clean,x.jurisdiction_raw,
                            x.location_raw,x.ownership_raw,
                            x.ownership_percent,x.footnote_refs_json,
                            x.schema_family,int(x.table_index),
                            int(x.source_row_index),x.raw_sha256,x.source_url,
                        ),
                    )
                    evidence_rows += 1

                    if x.dba_alias_raw:
                        alias_norm = normalize_text(x.dba_alias_raw)
                        alias_id = stable_id(
                            "idalias2",bucket_id,x.structured_row_id,
                            "dba",alias_norm
                        )
                        out.execute(
                            """
                            INSERT INTO identity_alias_evidence(
                              alias_evidence_id,identity_bucket_id,
                              structured_row_id,alias_type,alias_raw,
                              alias_normalized,evidence_available_at,
                              accession_number,evidence_role,
                              auto_merge_allowed,metadata_json
                            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                            """,
                            (
                                alias_id,bucket_id,x.structured_row_id,
                                "dba",x.dba_alias_raw,alias_norm,
                                x.evidence_available_at,x.accession_number,
                                "reported_dba_or_additional_name",0,
                                json.dumps({
                                    "identity_merge_performed": False,
                                    "source": "structured_ex21_row",
                                },sort_keys=True),
                            ),
                        )
                        alias_rows += 1

            out.execute(
                """
                UPDATE registry_runs
                SET status='completed',finished_at=CURRENT_TIMESTAMP,
                    buckets_written=?,evidence_rows_written=?,
                    alias_evidence_rows_written=?
                WHERE registry_run_id=?
                """,
                (len(grouped),evidence_rows,alias_rows,run_id),
            )
            out.commit()
        except Exception:
            out.rollback()
            raise

    return {
        "status":"PASS",
        "registry_run_id":run_id,
        "source_rows":len(obs),
        "evidence_buckets_written":len(grouped),
        "evidence_rows_written":evidence_rows,
        "alias_evidence_rows_written":alias_rows,
        "canonical_entities_created":False,
        "cross_registrant_merge":False,
        "cross_jurisdiction_merge":False,
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

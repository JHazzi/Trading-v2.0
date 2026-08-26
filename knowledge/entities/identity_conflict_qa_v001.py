from __future__ import annotations

import argparse
import hashlib
import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/event_graph_identity_conflict_qa_v001.json"


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join(str(x) for x in parts).encode("utf-8")
    return prefix + "_" + hashlib.sha256(raw).hexdigest()


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg["version"] != "event_graph_identity_conflict_qa_v001":
        raise ValueError("unexpected conflict QA version")
    if not all(cfg["hard_guards"].values()):
        raise ValueError("all conflict QA hard guards must remain enabled")
    if cfg["classification_contract"]["automatic_merge"]:
        raise ValueError("automatic merge must remain disabled")
    if cfg["classification_contract"]["automatic_split"]:
        raise ValueError("automatic split must remain disabled")
    return cfg


def plan(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = load_config(config_path)
    reg_db = ROOT / cfg["registry_db"]
    rows_db = ROOT / cfg["structured_rows_db"]
    if not reg_db.is_file():
        raise FileNotFoundError(reg_db)
    if not rows_db.is_file():
        raise FileNotFoundError(rows_db)

    with sqlite3.connect(f"file:{reg_db.resolve()}?mode=ro", uri=True) as c:
        buckets = c.execute(
            "SELECT COUNT(*) FROM identity_evidence_buckets"
        ).fetchone()[0]
        evidence = c.execute(
            "SELECT COUNT(*) FROM identity_bucket_evidence"
        ).fetchone()[0]
        aliases = c.execute(
            "SELECT COUNT(*) FROM identity_alias_evidence"
        ).fetchone()[0]
        missing = c.execute(
            """
            SELECT COUNT(*) FROM identity_evidence_buckets
            WHERE jurisdiction_status='missing'
            """
        ).fetchone()[0]
        conflict_groups = c.execute(
            """
            SELECT COUNT(*) FROM (
              SELECT registrant_entity_id, normalized_legal_name
              FROM identity_evidence_buckets
              GROUP BY registrant_entity_id, normalized_legal_name
              HAVING COUNT(DISTINCT normalized_jurisdiction) > 1
            )
            """
        ).fetchone()[0]
        conflict_buckets = c.execute(
            """
            SELECT COUNT(*) FROM identity_evidence_buckets b
            JOIN (
              SELECT registrant_entity_id, normalized_legal_name
              FROM identity_evidence_buckets
              GROUP BY registrant_entity_id, normalized_legal_name
              HAVING COUNT(DISTINCT normalized_jurisdiction) > 1
            ) g
            ON g.registrant_entity_id=b.registrant_entity_id
            AND g.normalized_legal_name=b.normalized_legal_name
            """
        ).fetchone()[0]

    with sqlite3.connect(f"file:{rows_db.resolve()}?mode=ro", uri=True) as c:
        source_rows = c.execute(
            "SELECT COUNT(*) FROM structured_ex21_rows"
        ).fetchone()[0]

    failures = []
    if buckets == 0 or evidence == 0:
        failures.append("empty_registry_v002")
    if evidence != source_rows:
        failures.append("registry_structured_row_count_mismatch")

    return {
        "status": "FAIL" if failures else "PASS",
        "failures": failures,
        "registry_buckets": int(buckets),
        "registry_evidence_rows": int(evidence),
        "structured_source_rows": int(source_rows),
        "alias_evidence_rows": int(aliases),
        "same_name_multi_jurisdiction_groups": int(conflict_groups),
        "same_name_multi_jurisdiction_buckets": int(conflict_buckets),
        "missing_jurisdiction_buckets": int(missing),
        "classification_contract": cfg["classification_contract"],
        "canonical_entities_created": False,
        "identity_merges_performed": False,
        "graph_edges_written": False,
        "next_gate": (
            "If PASS, build a complete conflict-evidence DB and JSON report. "
            "No identity or jurisdiction decision is written automatically."
        ),
    }


def ensure_schema(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE conflict_qa_runs(
      conflict_qa_run_id TEXT PRIMARY KEY,
      version TEXT NOT NULL,
      status TEXT NOT NULL,
      config_json TEXT NOT NULL,
      config_sha256 TEXT NOT NULL,
      started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      finished_at TEXT,
      conflict_groups_written INTEGER NOT NULL DEFAULT 0,
      conflict_buckets_written INTEGER NOT NULL DEFAULT 0,
      evidence_rows_written INTEGER NOT NULL DEFAULT 0,
      missing_buckets_written INTEGER NOT NULL DEFAULT 0,
      error_json TEXT
    );

    CREATE TABLE identity_conflict_groups(
      conflict_group_id TEXT PRIMARY KEY,
      conflict_qa_run_id TEXT NOT NULL,
      registrant_entity_id INTEGER NOT NULL,
      registrant_ticker TEXT NOT NULL,
      normalized_legal_name TEXT NOT NULL,
      bucket_count INTEGER NOT NULL,
      jurisdiction_count INTEGER NOT NULL,
      jurisdictions_json TEXT NOT NULL,
      total_evidence_rows INTEGER NOT NULL,
      first_evidence_available_at TEXT NOT NULL,
      last_evidence_available_at TEXT NOT NULL,
      shared_accession_pair_count INTEGER NOT NULL,
      temporal_pattern TEXT NOT NULL,
      decision_status TEXT NOT NULL DEFAULT 'unresolved',
      manual_label TEXT,
      manual_notes TEXT,
      metadata_json TEXT,
      UNIQUE(
        conflict_qa_run_id,registrant_entity_id,normalized_legal_name
      )
    );

    CREATE TABLE identity_conflict_buckets(
      conflict_bucket_id TEXT PRIMARY KEY,
      conflict_group_id TEXT NOT NULL,
      identity_bucket_id TEXT NOT NULL,
      display_legal_name TEXT NOT NULL,
      normalized_jurisdiction TEXT NOT NULL,
      display_jurisdiction TEXT,
      jurisdiction_status TEXT NOT NULL,
      evidence_occurrence_count INTEGER NOT NULL,
      accession_count INTEGER NOT NULL,
      first_evidence_available_at TEXT NOT NULL,
      last_evidence_available_at TEXT NOT NULL,
      dba_evidence_count INTEGER NOT NULL,
      ownership_evidence_count INTEGER NOT NULL,
      ownership_values_json TEXT NOT NULL,
      footnote_evidence_count INTEGER NOT NULL,
      UNIQUE(conflict_group_id,identity_bucket_id)
    );

    CREATE TABLE identity_conflict_evidence(
      conflict_evidence_id TEXT PRIMARY KEY,
      conflict_group_id TEXT NOT NULL,
      conflict_bucket_id TEXT NOT NULL,
      identity_bucket_id TEXT NOT NULL,
      structured_row_id TEXT NOT NULL,
      accession_number TEXT NOT NULL,
      evidence_available_at TEXT NOT NULL,
      legal_name_raw TEXT NOT NULL,
      jurisdiction_raw TEXT,
      location_raw TEXT,
      ownership_raw TEXT,
      ownership_percent REAL,
      dba_alias_raw TEXT,
      footnote_refs_json TEXT NOT NULL,
      schema_family TEXT NOT NULL,
      table_index INTEGER NOT NULL,
      source_row_index INTEGER NOT NULL,
      raw_sha256 TEXT NOT NULL,
      source_url TEXT,
      UNIQUE(conflict_bucket_id,structured_row_id)
    );

    CREATE TABLE conflict_bucket_pairs(
      conflict_pair_id TEXT PRIMARY KEY,
      conflict_group_id TEXT NOT NULL,
      left_conflict_bucket_id TEXT NOT NULL,
      right_conflict_bucket_id TEXT NOT NULL,
      left_jurisdiction TEXT NOT NULL,
      right_jurisdiction TEXT NOT NULL,
      shared_accession_count INTEGER NOT NULL,
      shared_accessions_json TEXT NOT NULL,
      evidence_ranges_overlap INTEGER NOT NULL,
      left_before_right INTEGER NOT NULL,
      right_before_left INTEGER NOT NULL,
      temporal_relation TEXT NOT NULL,
      dba_cross_match_count INTEGER NOT NULL,
      decision_status TEXT NOT NULL DEFAULT 'unresolved',
      manual_label TEXT,
      manual_notes TEXT,
      UNIQUE(
        conflict_group_id,left_conflict_bucket_id,right_conflict_bucket_id
      )
    );

    CREATE TABLE missing_jurisdiction_buckets(
      missing_bucket_id TEXT PRIMARY KEY,
      conflict_qa_run_id TEXT NOT NULL,
      identity_bucket_id TEXT NOT NULL,
      registrant_entity_id INTEGER NOT NULL,
      registrant_ticker TEXT NOT NULL,
      normalized_legal_name TEXT NOT NULL,
      display_legal_name TEXT NOT NULL,
      evidence_occurrence_count INTEGER NOT NULL,
      accession_count INTEGER NOT NULL,
      first_evidence_available_at TEXT NOT NULL,
      last_evidence_available_at TEXT NOT NULL,
      evidence_rows_json TEXT NOT NULL,
      decision_status TEXT NOT NULL DEFAULT 'unresolved'
    );

    CREATE INDEX idx_conflict_group_name
      ON identity_conflict_groups(registrant_entity_id,normalized_legal_name);
    CREATE INDEX idx_conflict_pair_group
      ON conflict_bucket_pairs(conflict_group_id,temporal_relation);
    """)


def _bucket_evidence(reg: sqlite3.Connection, bucket_id: str) -> list[dict[str, Any]]:
    rows = reg.execute(
        """
        SELECT
          e.structured_row_id,e.accession_number,e.corpus_document_id,
          e.evidence_available_at,e.legal_name_raw,e.legal_name_clean,
          e.jurisdiction_raw,e.location_raw,e.ownership_raw,e.ownership_percent,
          e.footnote_refs_json,e.schema_family,e.table_index,e.source_row_index,
          e.raw_sha256,e.source_url,
          a.alias_raw
        FROM identity_bucket_evidence e
        LEFT JOIN identity_alias_evidence a
          ON a.identity_bucket_id=e.identity_bucket_id
         AND a.structured_row_id=e.structured_row_id
        WHERE e.identity_bucket_id=?
        ORDER BY e.evidence_available_at,e.accession_number,
                 e.table_index,e.source_row_index,e.structured_row_id
        """,
        (bucket_id,),
    ).fetchall()
    names = [
        "structured_row_id","accession_number","corpus_document_id",
        "evidence_available_at","legal_name_raw","legal_name_clean",
        "jurisdiction_raw","location_raw","ownership_raw","ownership_percent",
        "footnote_refs_json","schema_family","table_index","source_row_index",
        "raw_sha256","source_url","dba_alias_raw",
    ]
    return [dict(zip(names, r)) for r in rows]


def _pair_temporal(a: dict, b: dict) -> tuple[str, int, int, int]:
    a_first, a_last = a["first"], a["last"]
    b_first, b_last = b["first"], b["last"]
    overlap = int(not (a_last < b_first or b_last < a_first))
    left_before = int(a_last < b_first)
    right_before = int(b_last < a_first)
    if left_before:
        rel = "left_before_right_no_overlap"
    elif right_before:
        rel = "right_before_left_no_overlap"
    else:
        rel = "evidence_ranges_overlap"
    return rel, overlap, left_before, right_before


def build(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = load_config(config_path)
    pre = plan(config_path)
    if pre["status"] != "PASS":
        raise RuntimeError(pre["failures"])

    reg_db = ROOT / cfg["registry_db"]
    out_db = ROOT / cfg["output_db"]
    cfg_json = json.dumps(cfg, sort_keys=True)
    cfg_sha = hashlib.sha256(cfg_json.encode("utf-8")).hexdigest()
    run_id = stable_id("idconfqa", cfg["version"], cfg_sha)

    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()

    report_dir = ROOT / cfg["report_dir"]
    report_dir.mkdir(parents=True, exist_ok=True)

    group_report = []
    groups_written = buckets_written = evidence_written = missing_written = 0

    with sqlite3.connect(f"file:{reg_db.resolve()}?mode=ro", uri=True) as reg, sqlite3.connect(out_db) as out:
        ensure_schema(out)
        out.execute(
            """
            INSERT INTO conflict_qa_runs(
              conflict_qa_run_id,version,status,config_json,config_sha256
            ) VALUES (?,?,?,?,?)
            """,
            (run_id,cfg["version"],"running",cfg_json,cfg_sha),
        )

        group_rows = reg.execute(
            """
            SELECT
              registrant_entity_id,registrant_ticker,normalized_legal_name
            FROM identity_evidence_buckets
            GROUP BY registrant_entity_id,registrant_ticker,normalized_legal_name
            HAVING COUNT(DISTINCT normalized_jurisdiction)>1
            ORDER BY registrant_ticker,normalized_legal_name
            """
        ).fetchall()

        for reg_id, ticker, norm_name in group_rows:
            bucket_rows = reg.execute(
                """
                SELECT
                  identity_bucket_id,display_legal_name,
                  normalized_jurisdiction,display_jurisdiction,
                  jurisdiction_status,evidence_occurrence_count,
                  accession_count,first_evidence_available_at,
                  last_evidence_available_at,dba_evidence_count,
                  ownership_evidence_count,ownership_values_json,
                  footnote_evidence_count
                FROM identity_evidence_buckets
                WHERE registrant_entity_id=? AND normalized_legal_name=?
                ORDER BY normalized_jurisdiction,identity_bucket_id
                """,
                (reg_id,norm_name),
            ).fetchall()

            bucket_objs = []
            total_evidence = 0
            firsts, lasts = [], []
            for row in bucket_rows:
                (
                    bucket_id,display_name,norm_juri,display_juri,juri_status,
                    occ_count,accession_count,first_at,last_at,dba_count,
                    ownership_count,ownership_values_json,footnote_count
                ) = row
                ev = _bucket_evidence(reg, bucket_id)
                total_evidence += len(ev)
                firsts.append(str(first_at))
                lasts.append(str(last_at))
                bucket_objs.append({
                    "identity_bucket_id": str(bucket_id),
                    "display_legal_name": str(display_name),
                    "normalized_jurisdiction": str(norm_juri),
                    "display_jurisdiction": display_juri,
                    "jurisdiction_status": str(juri_status),
                    "evidence_occurrence_count": int(occ_count),
                    "accession_count": int(accession_count),
                    "first": str(first_at),
                    "last": str(last_at),
                    "dba_evidence_count": int(dba_count),
                    "ownership_evidence_count": int(ownership_count),
                    "ownership_values_json": str(ownership_values_json),
                    "footnote_evidence_count": int(footnote_count),
                    "evidence": ev,
                })

            group_id = stable_id("idconfgrp",run_id,reg_id,norm_name)

            pair_payload = []
            shared_pair_count = 0
            temporal_patterns = Counter()
            for i in range(len(bucket_objs)):
                for j in range(i+1,len(bucket_objs)):
                    a,b = bucket_objs[i],bucket_objs[j]
                    acc_a = {x["accession_number"] for x in a["evidence"]}
                    acc_b = {x["accession_number"] for x in b["evidence"]}
                    shared = sorted(acc_a & acc_b)
                    if shared:
                        shared_pair_count += 1
                    rel, overlap, left_before, right_before = _pair_temporal(a,b)
                    temporal_patterns[rel] += 1

                    dba_a = {
                        str(x["dba_alias_raw"]).casefold()
                        for x in a["evidence"] if x["dba_alias_raw"]
                    }
                    dba_b = {
                        str(x["dba_alias_raw"]).casefold()
                        for x in b["evidence"] if x["dba_alias_raw"]
                    }
                    legal_a = a["display_legal_name"].casefold()
                    legal_b = b["display_legal_name"].casefold()
                    dba_cross = int(legal_b in dba_a) + int(legal_a in dba_b)

                    pair_id = stable_id(
                        "idconfpair",group_id,
                        a["identity_bucket_id"],b["identity_bucket_id"]
                    )
                    out.execute(
                        """
                        INSERT INTO conflict_bucket_pairs(
                          conflict_pair_id,conflict_group_id,
                          left_conflict_bucket_id,right_conflict_bucket_id,
                          left_jurisdiction,right_jurisdiction,
                          shared_accession_count,shared_accessions_json,
                          evidence_ranges_overlap,left_before_right,
                          right_before_left,temporal_relation,
                          dba_cross_match_count
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            pair_id,group_id,a["identity_bucket_id"],
                            b["identity_bucket_id"],
                            a["normalized_jurisdiction"],
                            b["normalized_jurisdiction"],
                            len(shared),json.dumps(shared),
                            overlap,left_before,right_before,rel,dba_cross,
                        ),
                    )
                    pair_payload.append({
                        "left_jurisdiction": a["display_jurisdiction"],
                        "right_jurisdiction": b["display_jurisdiction"],
                        "shared_accessions": shared,
                        "temporal_relation": rel,
                        "dba_cross_match_count": dba_cross,
                    })

            if temporal_patterns:
                temporal_pattern = ",".join(
                    f"{k}:{v}" for k,v in sorted(temporal_patterns.items())
                )
            else:
                temporal_pattern = "single_bucket_unexpected"

            out.execute(
                """
                INSERT INTO identity_conflict_groups(
                  conflict_group_id,conflict_qa_run_id,
                  registrant_entity_id,registrant_ticker,
                  normalized_legal_name,bucket_count,jurisdiction_count,
                  jurisdictions_json,total_evidence_rows,
                  first_evidence_available_at,last_evidence_available_at,
                  shared_accession_pair_count,temporal_pattern,metadata_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    group_id,run_id,int(reg_id),str(ticker),str(norm_name),
                    len(bucket_objs),
                    len({x["normalized_jurisdiction"] for x in bucket_objs}),
                    json.dumps(sorted({
                        x["display_jurisdiction"] for x in bucket_objs
                    }, key=lambda x: str(x))),
                    total_evidence,min(firsts),max(lasts),
                    shared_pair_count,temporal_pattern,
                    json.dumps({
                        "automatic_merge": False,
                        "automatic_split": False,
                    },sort_keys=True),
                ),
            )
            groups_written += 1

            for b in bucket_objs:
                conflict_bucket_id = stable_id(
                    "idconfbkt",group_id,b["identity_bucket_id"]
                )
                out.execute(
                    """
                    INSERT INTO identity_conflict_buckets(
                      conflict_bucket_id,conflict_group_id,identity_bucket_id,
                      display_legal_name,normalized_jurisdiction,
                      display_jurisdiction,jurisdiction_status,
                      evidence_occurrence_count,accession_count,
                      first_evidence_available_at,last_evidence_available_at,
                      dba_evidence_count,ownership_evidence_count,
                      ownership_values_json,footnote_evidence_count
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        conflict_bucket_id,group_id,b["identity_bucket_id"],
                        b["display_legal_name"],b["normalized_jurisdiction"],
                        b["display_jurisdiction"],b["jurisdiction_status"],
                        b["evidence_occurrence_count"],b["accession_count"],
                        b["first"],b["last"],b["dba_evidence_count"],
                        b["ownership_evidence_count"],
                        b["ownership_values_json"],
                        b["footnote_evidence_count"],
                    ),
                )
                buckets_written += 1

                for e in b["evidence"]:
                    evid_id = stable_id(
                        "idconfev",conflict_bucket_id,e["structured_row_id"]
                    )
                    out.execute(
                        """
                        INSERT INTO identity_conflict_evidence(
                          conflict_evidence_id,conflict_group_id,
                          conflict_bucket_id,identity_bucket_id,
                          structured_row_id,accession_number,
                          evidence_available_at,legal_name_raw,
                          jurisdiction_raw,location_raw,ownership_raw,
                          ownership_percent,dba_alias_raw,footnote_refs_json,
                          schema_family,table_index,source_row_index,
                          raw_sha256,source_url
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            evid_id,group_id,conflict_bucket_id,
                            b["identity_bucket_id"],e["structured_row_id"],
                            e["accession_number"],e["evidence_available_at"],
                            e["legal_name_raw"],e["jurisdiction_raw"],
                            e["location_raw"],e["ownership_raw"],
                            e["ownership_percent"],e["dba_alias_raw"],
                            e["footnote_refs_json"],e["schema_family"],
                            int(e["table_index"]),int(e["source_row_index"]),
                            e["raw_sha256"],e["source_url"],
                        ),
                    )
                    evidence_written += 1

            group_report.append({
                "conflict_group_id": group_id,
                "registrant_ticker": ticker,
                "normalized_legal_name": norm_name,
                "buckets": [
                    {
                        k: v for k,v in b.items()
                        if k != "evidence"
                    } | {
                        "evidence": b["evidence"][
                            :int(cfg["qa"]["max_evidence_rows_per_bucket_in_json"])
                        ]
                    }
                    for b in bucket_objs
                ],
                "pairs": pair_payload,
                "automatic_decision": None,
            })

        # Missing jurisdiction buckets.
        missing_rows = reg.execute(
            """
            SELECT
              identity_bucket_id,registrant_entity_id,registrant_ticker,
              normalized_legal_name,display_legal_name,
              evidence_occurrence_count,accession_count,
              first_evidence_available_at,last_evidence_available_at
            FROM identity_evidence_buckets
            WHERE jurisdiction_status='missing'
            ORDER BY registrant_ticker,normalized_legal_name
            """
        ).fetchall()

        missing_report = []
        for row in missing_rows:
            (
                bucket_id,reg_id,ticker,norm_name,display_name,
                occ_count,accession_count,first_at,last_at
            ) = row
            ev = _bucket_evidence(reg,bucket_id)
            missing_id = stable_id("idmissing",run_id,bucket_id)
            out.execute(
                """
                INSERT INTO missing_jurisdiction_buckets(
                  missing_bucket_id,conflict_qa_run_id,identity_bucket_id,
                  registrant_entity_id,registrant_ticker,
                  normalized_legal_name,display_legal_name,
                  evidence_occurrence_count,accession_count,
                  first_evidence_available_at,last_evidence_available_at,
                  evidence_rows_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    missing_id,run_id,bucket_id,int(reg_id),ticker,
                    norm_name,display_name,int(occ_count),
                    int(accession_count),first_at,last_at,
                    json.dumps(ev,ensure_ascii=False),
                ),
            )
            missing_written += 1
            missing_report.append({
                "identity_bucket_id": bucket_id,
                "registrant_ticker": ticker,
                "display_legal_name": display_name,
                "first_evidence_available_at": first_at,
                "last_evidence_available_at": last_at,
                "evidence": ev,
            })

        out.execute(
            """
            UPDATE conflict_qa_runs
            SET status='completed',finished_at=CURRENT_TIMESTAMP,
                conflict_groups_written=?,conflict_buckets_written=?,
                evidence_rows_written=?,missing_buckets_written=?
            WHERE conflict_qa_run_id=?
            """,
            (
                groups_written,buckets_written,evidence_written,
                missing_written,run_id
            ),
        )
        out.commit()

    report = {
        "status": "PASS",
        "conflict_groups": groups_written,
        "conflict_buckets": buckets_written,
        "conflict_evidence_rows": evidence_written,
        "missing_jurisdiction_buckets": missing_written,
        "groups": group_report,
        "missing_jurisdiction": missing_report,
        "automatic_merge": False,
        "automatic_split": False,
        "canonical_entities_created": False,
        "graph_edges_written": False,
    }
    (report_dir / "conflict_evidence.json").write_text(
        json.dumps(report,indent=2,ensure_ascii=False) + "\n",
        encoding="utf-8",
    )

    return {
        "status": "PASS",
        "conflict_qa_run_id": run_id,
        "conflict_groups_written": groups_written,
        "conflict_buckets_written": buckets_written,
        "conflict_evidence_rows_written": evidence_written,
        "missing_jurisdiction_buckets_written": missing_written,
        "report": str(report_dir / "conflict_evidence.json"),
        "canonical_entities_created": False,
        "identity_merges_performed": False,
        "graph_edges_written": False,
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

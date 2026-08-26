from __future__ import annotations

import argparse
import hashlib
import json
import re
import sqlite3
import unicodedata
from collections import Counter, defaultdict
from pathlib import Path


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = (
    ROOT / "config/event_graph_entity_registry_foundation_v001.json"
)


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = (
        text.replace("\u00a0", " ")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
    )
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n,;:.")
    return text.casefold()


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join(str(x) for x in parts).encode("utf-8")
    return prefix + "_" + hashlib.sha256(raw).hexdigest()


def load_config(path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg["version"] != "event_graph_entity_registry_foundation_v001":
        raise ValueError("unexpected registry version")
    if cfg["contract_claims_in_scope"] is not False:
        raise ValueError("contract claims must remain out of registry V001")
    identity = cfg["identity_contract"]
    if not identity["exact_normalized_name_grouping_only"]:
        raise ValueError("V001 requires exact normalized grouping")
    if identity["fuzzy_merge"] or identity["cross_name_alias_merge"]:
        raise ValueError("V001 forbids fuzzy/cross-name merge")
    return cfg


def quality_reason(raw_name: str, cfg: dict) -> str | None:
    norm = normalize_name(raw_name)
    bad = {
        normalize_name(x)
        for x in cfg["quality_contract"]["reject_known_headers"]
    }
    if norm in bad:
        return "known_header"
    if cfg["quality_contract"]["reject_generic_column_phrases"]:
        if any(
            token in norm
            for token in (
                "organized or incorporated",
                "where incorporated",
                "jurisdiction of",
                "percent of equity securities owned",
                "name under which doing business",
            )
        ):
            return "generic_column_phrase"
    if len(norm) < 3:
        return "too_short"
    return None


def source_rows(
    conn: sqlite3.Connection,
    cfg: dict,
) -> list[dict]:
    tables = {
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    if "evidence_claims" not in tables:
        raise RuntimeError("evidence_claims table missing")

    rows = conn.execute(
        """
        SELECT
          evidence_claim_id,
          claim_kind,
          corpus_document_id,
          content_raw_document_id,
          accession_number,
          document_class,
          registrant_asset_id,
          registrant_entity_id,
          registrant_ticker,
          named_entity_raw,
          named_entity_normalized,
          resolved_named_entity_id,
          resolution_status,
          resolution_method,
          matched_alias,
          evidence_available_at,
          availability_is_point_in_time,
          evidence_char_start,
          evidence_char_end,
          evidence_text,
          extraction_method,
          raw_sha256,
          source_url
        FROM evidence_claims
        WHERE claim_kind=?
        ORDER BY
          evidence_available_at,
          accession_number,
          evidence_claim_id
        """,
        (cfg["input_claim_kind"],),
    ).fetchall()
    names = [
        "evidence_claim_id","claim_kind","corpus_document_id",
        "content_raw_document_id","accession_number","document_class",
        "registrant_asset_id","registrant_entity_id","registrant_ticker",
        "named_entity_raw","named_entity_normalized",
        "resolved_named_entity_id","resolution_status",
        "resolution_method","matched_alias","evidence_available_at",
        "availability_is_point_in_time","evidence_char_start",
        "evidence_char_end","evidence_text","extraction_method",
        "raw_sha256","source_url",
    ]
    return [dict(zip(names, r)) for r in rows]


def plan(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = load_config(config_path)
    evidence_db = ROOT / cfg["relation_evidence_db"]
    if not evidence_db.is_file():
        raise FileNotFoundError(evidence_db)

    with sqlite3.connect(
        f"file:{evidence_db.resolve()}?mode=ro", uri=True
    ) as conn:
        rows = source_rows(conn, cfg)

    accepted = []
    rejected = []
    for r in rows:
        reason = quality_reason(r["named_entity_raw"], cfg)
        if reason is None:
            accepted.append(r)
        else:
            rejected.append((r, reason))

    failures = []
    if not rows:
        failures.append("zero_input_ex21_claims")
    if any(int(r["availability_is_point_in_time"] or 0) != 0 for r in rows):
        failures.append("input_claim_incorrectly_marked_pit")
    if not accepted:
        failures.append("zero_accepted_registry_names")

    unique_names = {
        normalize_name(r["named_entity_raw"]) for r in accepted
    }
    source_entities = {
        int(r["registrant_entity_id"]) for r in accepted
    }
    return {
        "status": "FAIL" if failures else "PASS",
        "failures": failures,
        "input_claims": len(rows),
        "accepted_claims": len(accepted),
        "rejected_claims": len(rejected),
        "rejection_reasons": dict(Counter(x[1] for x in rejected)),
        "unique_exact_normalized_names": len(unique_names),
        "registrant_entities": len(source_entities),
        "contract_claims_in_scope": False,
        "strict_historical_pit": False,
        "main_db_mutated": False,
        "graph_edges_written": False,
        "identity_contract": cfg["identity_contract"],
        "next_gate": (
            "If PASS, build a separate evidence-backed name registry. "
            "No canonical entity creation or alias merging occurs."
        ),
    }


def ensure_schema(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE registry_runs(
      registry_run_id TEXT PRIMARY KEY,
      registry_version TEXT NOT NULL,
      status TEXT NOT NULL,
      configuration_json TEXT NOT NULL,
      configuration_sha256 TEXT NOT NULL,
      started_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP,
      finished_at TEXT,
      source_claims INTEGER NOT NULL DEFAULT 0,
      accepted_claims INTEGER NOT NULL DEFAULT 0,
      name_records INTEGER NOT NULL DEFAULT 0,
      error_json TEXT
    );

    CREATE TABLE registry_name_records(
      registry_name_id TEXT PRIMARY KEY,
      registry_run_id TEXT NOT NULL,
      normalized_name TEXT NOT NULL,
      display_name TEXT NOT NULL,
      evidence_occurrence_count INTEGER NOT NULL,
      first_evidence_available_at TEXT NOT NULL,
      last_evidence_available_at TEXT NOT NULL,
      source_registrant_count INTEGER NOT NULL,
      accession_count INTEGER NOT NULL,
      any_existing_exact_resolution INTEGER NOT NULL,
      existing_resolved_entity_ids_json TEXT NOT NULL,
      identity_status TEXT NOT NULL,
      metadata_json TEXT,
      UNIQUE(registry_run_id, normalized_name)
    );

    CREATE TABLE registry_name_evidence(
      registry_evidence_id TEXT PRIMARY KEY,
      registry_run_id TEXT NOT NULL,
      registry_name_id TEXT NOT NULL,
      evidence_claim_id TEXT NOT NULL,
      accession_number TEXT NOT NULL,
      registrant_entity_id INTEGER NOT NULL,
      registrant_asset_id INTEGER NOT NULL,
      registrant_ticker TEXT,
      raw_name TEXT NOT NULL,
      evidence_available_at TEXT NOT NULL,
      availability_is_point_in_time INTEGER NOT NULL,
      evidence_text TEXT NOT NULL,
      raw_sha256 TEXT NOT NULL,
      source_url TEXT,
      original_resolution_status TEXT NOT NULL,
      original_resolved_entity_id INTEGER,
      FOREIGN KEY(registry_name_id)
        REFERENCES registry_name_records(registry_name_id)
        ON DELETE CASCADE,
      UNIQUE(registry_run_id,evidence_claim_id)
    );

    CREATE TABLE registry_rejections(
      registry_rejection_id TEXT PRIMARY KEY,
      registry_run_id TEXT NOT NULL,
      evidence_claim_id TEXT NOT NULL,
      raw_name TEXT NOT NULL,
      normalized_name TEXT NOT NULL,
      rejection_reason TEXT NOT NULL,
      accession_number TEXT NOT NULL,
      registrant_ticker TEXT,
      evidence_available_at TEXT NOT NULL,
      evidence_text TEXT NOT NULL,
      UNIQUE(registry_run_id,evidence_claim_id)
    );

    CREATE INDEX idx_registry_name_normalized
      ON registry_name_records(normalized_name);
    CREATE INDEX idx_registry_evidence_time
      ON registry_name_evidence(evidence_available_at);
    CREATE INDEX idx_registry_evidence_registrant
      ON registry_name_evidence(registrant_entity_id,evidence_available_at);
    """)


def build(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = load_config(config_path)
    pre = plan(config_path)
    if pre["status"] != "PASS":
        raise RuntimeError(pre["failures"])

    evidence_db = ROOT / cfg["relation_evidence_db"]
    out_db = ROOT / cfg["output_db"]
    cfg_json = json.dumps(cfg, sort_keys=True)
    cfg_sha = hashlib.sha256(cfg_json.encode("utf-8")).hexdigest()
    run_id = stable_id("entityreg", cfg["version"], cfg_sha)

    with sqlite3.connect(
        f"file:{evidence_db.resolve()}?mode=ro", uri=True
    ) as src:
        rows = source_rows(src, cfg)

    accepted_by_name = defaultdict(list)
    rejected = []
    for row in rows:
        reason = quality_reason(row["named_entity_raw"], cfg)
        if reason is None:
            accepted_by_name[
                normalize_name(row["named_entity_raw"])
            ].append(row)
        else:
            rejected.append((row, reason))

    out_db.parent.mkdir(parents=True, exist_ok=True)
    if out_db.exists():
        out_db.unlink()

    with sqlite3.connect(out_db) as out:
        out.execute("PRAGMA foreign_keys=ON")
        ensure_schema(out)
        out.execute(
            """
            INSERT INTO registry_runs(
              registry_run_id,registry_version,status,
              configuration_json,configuration_sha256,
              source_claims
            ) VALUES (?,?,?,?,?,?)
            """,
            (
                run_id,cfg["version"],"running",
                cfg_json,cfg_sha,len(rows)
            ),
        )
        out.commit()

        try:
            evidence_written = 0
            for norm, items in sorted(accepted_by_name.items()):
                # Deterministic display form: most frequent raw spelling,
                # lexical tie-break. This is a label, not identity merging.
                raw_counts = Counter(
                    str(x["named_entity_raw"]) for x in items
                )
                display_name = sorted(
                    raw_counts,
                    key=lambda x: (-raw_counts[x], x.casefold(), x)
                )[0]
                first = min(str(x["evidence_available_at"]) for x in items)
                last = max(str(x["evidence_available_at"]) for x in items)
                registrants = {
                    int(x["registrant_entity_id"]) for x in items
                }
                accessions = {
                    str(x["accession_number"]) for x in items
                }
                existing_ids = sorted({
                    int(x["resolved_named_entity_id"])
                    for x in items
                    if x["resolved_named_entity_id"] is not None
                })
                identity_status = (
                    "existing_exact_entity_observed"
                    if len(existing_ids) == 1
                    else (
                        "conflicting_existing_exact_entities"
                        if len(existing_ids) > 1
                        else "unresolved_name_registry_record"
                    )
                )
                name_id = stable_id("regname", run_id, norm)
                out.execute(
                    """
                    INSERT INTO registry_name_records(
                      registry_name_id,registry_run_id,normalized_name,
                      display_name,evidence_occurrence_count,
                      first_evidence_available_at,last_evidence_available_at,
                      source_registrant_count,accession_count,
                      any_existing_exact_resolution,
                      existing_resolved_entity_ids_json,
                      identity_status,metadata_json
                    ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        name_id,run_id,norm,display_name,len(items),
                        first,last,len(registrants),len(accessions),
                        int(bool(existing_ids)),
                        json.dumps(existing_ids),
                        identity_status,
                        json.dumps({
                            "canonical_entity_created": False,
                            "cross_name_alias_merge_performed": False,
                            "source_claim_kind":
                                cfg["input_claim_kind"],
                        }, sort_keys=True),
                    ),
                )

                for item in items:
                    ev_id = stable_id(
                        "regev",run_id,item["evidence_claim_id"]
                    )
                    out.execute(
                        """
                        INSERT INTO registry_name_evidence(
                          registry_evidence_id,registry_run_id,
                          registry_name_id,evidence_claim_id,
                          accession_number,registrant_entity_id,
                          registrant_asset_id,registrant_ticker,
                          raw_name,evidence_available_at,
                          availability_is_point_in_time,
                          evidence_text,raw_sha256,source_url,
                          original_resolution_status,
                          original_resolved_entity_id
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            ev_id,run_id,name_id,item["evidence_claim_id"],
                            item["accession_number"],
                            int(item["registrant_entity_id"]),
                            int(item["registrant_asset_id"]),
                            item["registrant_ticker"],
                            item["named_entity_raw"],
                            item["evidence_available_at"],0,
                            item["evidence_text"],item["raw_sha256"],
                            item["source_url"],
                            item["resolution_status"],
                            item["resolved_named_entity_id"],
                        ),
                    )
                    evidence_written += 1

            for item, reason in rejected:
                rid = stable_id(
                    "regreject",run_id,item["evidence_claim_id"]
                )
                out.execute(
                    """
                    INSERT INTO registry_rejections(
                      registry_rejection_id,registry_run_id,
                      evidence_claim_id,raw_name,normalized_name,
                      rejection_reason,accession_number,
                      registrant_ticker,evidence_available_at,
                      evidence_text
                    ) VALUES (?,?,?,?,?,?,?,?,?,?)
                    """,
                    (
                        rid,run_id,item["evidence_claim_id"],
                        item["named_entity_raw"],
                        normalize_name(item["named_entity_raw"]),
                        reason,item["accession_number"],
                        item["registrant_ticker"],
                        item["evidence_available_at"],
                        item["evidence_text"],
                    ),
                )

            out.execute(
                """
                UPDATE registry_runs
                SET status='completed',finished_at=CURRENT_TIMESTAMP,
                    accepted_claims=?,name_records=?
                WHERE registry_run_id=?
                """,
                (
                    evidence_written,len(accepted_by_name),run_id
                ),
            )
            out.commit()
        except Exception as exc:
            out.rollback()
            raise

    return {
        "status":"PASS",
        "registry_run_id":run_id,
        "source_claims":len(rows),
        "accepted_evidence_rows":sum(
            len(v) for v in accepted_by_name.values()
        ),
        "name_records":len(accepted_by_name),
        "rejected_claims":len(rejected),
        "rejection_reasons":dict(Counter(x[1] for x in rejected)),
        "existing_exact_entity_name_records":sum(
            1 for items in accepted_by_name.values()
            if any(x["resolved_named_entity_id"] is not None for x in items)
        ),
        "strict_historical_pit":False,
        "contract_claims_ingested":False,
        "main_db_mutated":False,
        "graph_edges_written":False,
        "canonical_entities_created":False,
    }


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--config",type=Path,default=DEFAULT_CONFIG)
    p.add_argument("--stage",required=True,choices=("plan","build"))
    a=p.parse_args()
    result=plan(a.config) if a.stage=="plan" else build(a.config)
    print(json.dumps(result,indent=2))


if __name__=="__main__":
    main()

from __future__ import annotations

import argparse
import json
import re
import sqlite3
from pathlib import Path
from typing import Any


ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = (
    ROOT / "config" / "event_graph_relation_source_audit_v001.json"
)
DEFAULT_REPORT = (
    ROOT / "reports" / "event_graph_relation_source_audit_v001.json"
)

NAME_HINTS = (
    "sec",
    "filing",
    "document",
    "news",
    "event",
    "evidence",
    "source",
    "submission",
    "article",
    "cluster",
)

TEXT_HINTS = (
    "text",
    "title",
    "summary",
    "body",
    "content",
    "payload",
    "json",
    "description",
    "snippet",
)

TIME_HINTS = (
    "available_at",
    "published_at",
    "filed_at",
    "accepted_at",
    "retrieved_at",
    "observed_at",
    "ingested_at",
    "state_time",
    "first_seen_at",
    "event_time",
    "timestamp",
    "time",
    "date",
)

PIT_HINTS = (
    "point_in_time",
    "pit",
    "available_at",
    "availability",
    "revision",
    "version",
    "retrieval",
    "retrieved_at",
)

ID_HINTS = (
    "asset_id",
    "ticker",
    "cik",
    "entity_id",
    "event_id",
    "news_id",
    "document_id",
    "source_document_id",
    "accession",
    "filing_id",
)


def quote_ident(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def tables(conn: sqlite3.Connection) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            """
            SELECT name
            FROM sqlite_master
            WHERE type='table'
              AND name NOT LIKE 'sqlite_%'
            ORDER BY name
            """
        ).fetchall()
    ]


def columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [
        {
            "cid": int(r[0]),
            "name": str(r[1]),
            "type": str(r[2] or ""),
            "notnull": bool(r[3]),
            "default": r[4],
            "pk": bool(r[5]),
        }
        for r in conn.execute(
            f"PRAGMA table_info({quote_ident(table)})"
        ).fetchall()
    ]


def row_count(conn: sqlite3.Connection, table: str) -> int:
    return int(
        conn.execute(
            f"SELECT COUNT(*) FROM {quote_ident(table)}"
        ).fetchone()[0]
    )


def non_null_count(
    conn: sqlite3.Connection,
    table: str,
    column: str,
) -> int:
    return int(
        conn.execute(
            f"""
            SELECT COUNT(*)
            FROM {quote_ident(table)}
            WHERE {quote_ident(column)} IS NOT NULL
            """
        ).fetchone()[0]
    )


def safe_min_max(
    conn: sqlite3.Connection,
    table: str,
    column: str,
) -> dict[str, Any]:
    try:
        row = conn.execute(
            f"""
            SELECT
              MIN({quote_ident(column)}),
              MAX({quote_ident(column)}),
              COUNT({quote_ident(column)})
            FROM {quote_ident(table)}
            """
        ).fetchone()
        return {
            "min": row[0],
            "max": row[1],
            "non_null": int(row[2] or 0),
        }
    except sqlite3.Error as exc:
        return {"error": str(exc)}


def distinct_count(
    conn: sqlite3.Connection,
    table: str,
    column: str,
) -> int | None:
    try:
        return int(
            conn.execute(
                f"""
                SELECT COUNT(DISTINCT {quote_ident(column)})
                FROM {quote_ident(table)}
                WHERE {quote_ident(column)} IS NOT NULL
                """
            ).fetchone()[0]
        )
    except sqlite3.Error:
        return None


def table_score(name: str, colnames: set[str]) -> tuple[int, list[str]]:
    lname = name.lower()
    reasons: list[str] = []
    score = 0

    for hint in NAME_HINTS:
        if hint in lname:
            score += 2
            reasons.append(f"name:{hint}")

    text_cols = [
        c for c in colnames
        if any(h in c.lower() for h in TEXT_HINTS)
    ]
    time_cols = [
        c for c in colnames
        if any(h == c.lower() or h in c.lower() for h in TIME_HINTS)
    ]
    pit_cols = [
        c for c in colnames
        if any(h in c.lower() for h in PIT_HINTS)
    ]
    id_cols = [
        c for c in colnames
        if any(h == c.lower() or h in c.lower() for h in ID_HINTS)
    ]

    if text_cols:
        score += 2
        reasons.append("has_text_or_payload")
    if time_cols:
        score += 2
        reasons.append("has_time")
    if pit_cols:
        score += 3
        reasons.append("has_pit_or_version_provenance")
    if id_cols:
        score += 2
        reasons.append("has_resolvable_ids")

    if "raw_text" in colnames or "content" in colnames or "body" in colnames:
        score += 3
        reasons.append("has_full_text_candidate")
    if "source_ref" in colnames or "source_url" in colnames:
        score += 2
        reasons.append("has_source_reference")
    if "available_at" in colnames:
        score += 4
        reasons.append("explicit_available_at")
    if "point_in_time_verified" in colnames:
        score += 3
        reasons.append("explicit_pit_flag")

    return score, reasons


def inspect_table(
    conn: sqlite3.Connection,
    table: str,
) -> dict[str, Any]:
    cols = columns(conn, table)
    names = {c["name"] for c in cols}
    score, reasons = table_score(table, names)
    count = row_count(conn, table)

    time_columns = [
        c["name"] for c in cols
        if any(
            h == c["name"].lower() or h in c["name"].lower()
            for h in TIME_HINTS
        )
    ]
    text_columns = [
        c["name"] for c in cols
        if any(h in c["name"].lower() for h in TEXT_HINTS)
    ]
    identity_columns = [
        c["name"] for c in cols
        if any(
            h == c["name"].lower() or h in c["name"].lower()
            for h in ID_HINTS
        )
    ]
    pit_columns = [
        c["name"] for c in cols
        if any(h in c["name"].lower() for h in PIT_HINTS)
    ]

    # Avoid huge scans: MIN/MAX and distinct only on likely relevant cols.
    time_coverage = {
        c: safe_min_max(conn, table, c)
        for c in time_columns[:8]
    }
    identity_cardinality = {
        c: distinct_count(conn, table, c)
        for c in identity_columns[:8]
    }
    text_non_null = {
        c: non_null_count(conn, table, c)
        for c in text_columns[:8]
    }

    return {
        "table": table,
        "rows": count,
        "score": score,
        "reasons": reasons,
        "columns": cols,
        "time_columns": time_columns,
        "time_coverage": time_coverage,
        "pit_or_version_columns": pit_columns,
        "identity_columns": identity_columns,
        "identity_cardinality": identity_cardinality,
        "text_or_payload_columns": text_columns,
        "text_non_null": text_non_null,
    }


def foreign_keys(
    conn: sqlite3.Connection,
    table: str,
) -> list[dict[str, Any]]:
    out = []
    for r in conn.execute(
        f"PRAGMA foreign_key_list({quote_ident(table)})"
    ).fetchall():
        out.append(
            {
                "from": r[3],
                "to_table": r[2],
                "to_column": r[4],
            }
        )
    return out


def migration_history(conn: sqlite3.Connection) -> list[dict[str, str]]:
    names = set(tables(conn))
    if "schema_migrations" not in names:
        return []
    try:
        rows = conn.execute(
            """
            SELECT version,name
            FROM schema_migrations
            ORDER BY
              CASE
                WHEN version GLOB '[0-9]*'
                THEN CAST(version AS INTEGER)
                ELSE 2147483647
              END,
              version
            """
        ).fetchall()
        return [{"version": str(v), "name": str(n)} for v, n in rows]
    except sqlite3.Error:
        return []


def classify_candidate(t: dict[str, Any]) -> dict[str, Any]:
    cols = {x["name"] for x in t["columns"]}
    # A source can have a strong causal-clock candidate without using the
    # literal name `available_at`. SEC acceptance/filing timestamps and
    # publication timestamps are worth prioritizing for semantic review, but
    # this audit does NOT declare them PIT-verified merely from the column name.
    causal_time_candidate = any(
        x in cols
        for x in (
            "available_at",
            "evidence_available_at",
            "state_time",
            "accepted_at",
            "filed_at",
            "filing_date",
            "published_at",
        )
    )
    explicit_availability = (
        "available_at" in cols
        or "evidence_available_at" in cols
    )
    raw_text = any(
        x in cols for x in ("raw_text", "body", "content", "text")
    )
    source_ref = any(
        x in cols
        for x in (
            "source_ref",
            "source_url",
            "canonical_url",
            "accession",
            "accession_number",
            "document_id",
            "source_document_id",
        )
    )
    asset_resolvable = any(
        x in cols for x in ("asset_id", "ticker", "cik", "entity_id")
    )
    versioned = any(
        ("version" in x.lower() or "revision" in x.lower())
        for x in cols
    )

    readiness = 0
    flags = []
    if causal_time_candidate:
        readiness += 3
        flags.append("causal_time_candidate")
    if raw_text:
        readiness += 3
        flags.append("full_text_candidate")
    if source_ref:
        readiness += 2
        flags.append("source_reference_candidate")
    if asset_resolvable:
        readiness += 2
        flags.append("entity_resolution_candidate")
    if versioned:
        readiness += 1
        flags.append("version_or_revision_candidate")
    if t["rows"] > 0:
        readiness += 1
        flags.append("non_empty")

    if readiness >= 10:
        tier = "A"
    elif readiness >= 7:
        tier = "B"
    elif readiness >= 4:
        tier = "C"
    else:
        tier = "D"

    return {
        "readiness_score": readiness,
        "tier": tier,
        "flags": flags,
        "causal_time_candidate": causal_time_candidate,
        "explicit_availability_candidate": explicit_availability,
        "pit_verified_by_audit": False,
        "full_text_candidate": raw_text,
        "source_reference_candidate": source_ref,
        "entity_resolution_candidate": asset_resolvable,
        "version_or_revision_candidate": versioned,
    }


def audit(
    db: Path,
) -> dict[str, Any]:
    if not db.is_file():
        raise FileNotFoundError(db)

    uri = f"file:{db.resolve()}?mode=ro"
    with sqlite3.connect(uri, uri=True) as conn:
        all_tables = tables(conn)
        inspected = [inspect_table(conn, t) for t in all_tables]

        # Candidate source inventory is broad by design, then ranked.
        candidates = []
        for t in inspected:
            if t["score"] < 4:
                continue
            x = dict(t)
            x["foreign_keys"] = foreign_keys(conn, t["table"])
            x["relation_source_readiness"] = classify_candidate(t)
            candidates.append(x)

        candidates.sort(
            key=lambda x: (
                -x["relation_source_readiness"]["readiness_score"],
                -x["score"],
                -x["rows"],
                x["table"],
            )
        )

        tmap = {x["table"]: x for x in inspected}

        core = {}
        for name in (
            "assets",
            "entities",
            "asset_entities",
            "news_documents",
            "news_assets",
            "events",
            "event_news",
            "event_assets",
            "normalized_event_state_snapshots",
            "normalized_event_reaction_labels",
            "event_entity_links_v001",
            "temporal_relation_assertions_v001",
            "temporal_relation_observations_v001",
        ):
            if name in tmap:
                core[name] = {
                    "rows": tmap[name]["rows"],
                    "identity_cardinality": tmap[name]["identity_cardinality"],
                    "time_coverage": tmap[name]["time_coverage"],
                    "pit_or_version_columns": tmap[name][
                        "pit_or_version_columns"
                    ],
                    "text_or_payload_columns": tmap[name][
                        "text_or_payload_columns"
                    ],
                }

        migration = migration_history(conn)

    # Recommendations are based only on structural properties discovered.
    tier_a = [
        x["table"] for x in candidates
        if x["relation_source_readiness"]["tier"] == "A"
    ]
    tier_b = [
        x["table"] for x in candidates
        if x["relation_source_readiness"]["tier"] == "B"
    ]

    if tier_a:
        next_gate = (
            "Inspect Tier-A source semantics and exact availability fields, "
            "then preregister the first deterministic/LLM-assisted relation "
            "candidate extractor against those sources only."
        )
    elif tier_b:
        next_gate = (
            "No Tier-A source detected. Inspect Tier-B source semantics and "
            "determine whether causal availability can be reconstructed "
            "without inventing timestamps before relation extraction."
        )
    else:
        next_gate = (
            "No sufficiently relation-ready local source detected. Design a "
            "new official historical source acquisition contract before "
            "extracting relations."
        )

    return {
        "status": "PASS",
        "failures": [],
        "read_only": True,
        "database": str(db),
        "table_count": len(all_tables),
        "migration_history_tail": migration[-12:],
        "core_inventory": core,
        "relation_source_candidates": candidates,
        "summary": {
            "tier_a_sources": tier_a,
            "tier_b_sources": tier_b,
            "candidate_table_count": len(candidates),
        },
        "scientific_contract": {
            "database_mutated": False,
            "relations_extracted": False,
            "models_trained": False,
            "candidate_confidence_not_market_weight": True,
            "future_evidence_not_allowed": True,
            "source_semantics_must_be_reviewed_before_extraction": True,
        },
        "next_gate": next_gate,
    }


def main():
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    a = p.parse_args()

    cfg = json.loads(a.config.read_text(encoding="utf-8"))
    db = ROOT / cfg["database"]
    result = audit(db)

    a.report.parent.mkdir(parents=True, exist_ok=True)
    a.report.write_text(
        json.dumps(result, indent=2, default=str) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, default=str))


if __name__ == "__main__":
    main()

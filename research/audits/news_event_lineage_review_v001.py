from __future__ import annotations

import argparse
import json
import re
import sqlite3
from collections import defaultdict
from pathlib import Path
from typing import Any

CONTRACT_VERSION = "news_event_lineage_review_v001"

TARGET_TABLES = [
    "raw_source_documents",
    "news_documents",
    "news_assets",
    "news_features",
    "event_news",
    "event_cluster_news",
    "event_cluster_news_membership_refs",
    "event_clusters",
    "event_document_fingerprints",
    "event_evidence",
    "event_evidence_semantics",
    "event_source_knowledge",
    "events",
    "event_states",
    "event_feature_snapshots",
    "event_reaction_outcomes",
    "normalized_event_identities",
    "normalized_event_observations",
    "normalized_event_reaction_labels",
    "normalized_event_state_snapshots",
    "market_sessions",
    "price_bars",
    "event_clustering_configs",
    "event_clustering_runs",
    "event_normalization_configs",
    "event_normalization_runs",
    "event_state_feature_configs",
    "event_brain_training_runs",
]

CAUSAL_TIME_PRIORITY = [
    "available_at",
    "first_seen_at",
    "retrieved_at",
    "observed_at",
    "published_at",
    "timestamp",
    "created_at",
]

CODE_SUFFIXES = {".py", ".sql", ".md", ".json", ".yaml", ".yml", ".toml"}


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def ro_connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(path)
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    c = sqlite3.connect(uri, uri=True)
    c.row_factory = sqlite3.Row
    return c


def tables(conn: sqlite3.Connection) -> set[str]:
    return {str(r[0]) for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}


def columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [{
        "cid": r[0], "name": str(r[1]), "type": str(r[2]),
        "notnull": int(r[3]), "default": r[4], "pk": int(r[5]),
    } for r in conn.execute(f'PRAGMA table_info({q(table)})')]


def colnames(conn: sqlite3.Connection, table: str) -> list[str]:
    return [x["name"] for x in columns(conn, table)]


def foreign_keys(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    out = []
    for r in conn.execute(f'PRAGMA foreign_key_list({q(table)})'):
        out.append({
            "from_table": table,
            "from_column": str(r[3]),
            "to_table": str(r[2]),
            "to_column": str(r[4]),
        })
    return out


def count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM {q(table)}').fetchone()[0])


def distinct_count(conn: sqlite3.Connection, table: str, col: str) -> int:
    return int(conn.execute(
        f'SELECT COUNT(DISTINCT {q(col)}) FROM {q(table)} WHERE {q(col)} IS NOT NULL'
    ).fetchone()[0] or 0)


def nonnull_count(conn: sqlite3.Connection, table: str, col: str) -> int:
    return int(conn.execute(
        f'SELECT COUNT(*) FROM {q(table)} WHERE {q(col)} IS NOT NULL '
        f'AND TRIM(CAST({q(col)} AS TEXT))<>""'
    ).fetchone()[0] or 0)


def minmax(conn: sqlite3.Connection, table: str, col: str) -> dict[str, Any]:
    r = conn.execute(
        f'SELECT MIN({q(col)}), MAX({q(col)}), COUNT({q(col)}) FROM {q(table)}'
    ).fetchone()
    return {"min": r[0], "max": r[1], "non_null": int(r[2] or 0)}


def top_values(conn: sqlite3.Connection, table: str, col: str, limit: int = 20) -> list[dict[str, Any]]:
    rows = conn.execute(
        f'SELECT CAST({q(col)} AS TEXT) v, COUNT(*) n FROM {q(table)} '
        f'WHERE {q(col)} IS NOT NULL AND TRIM(CAST({q(col)} AS TEXT))<>"" '
        f'GROUP BY {q(col)} ORDER BY n DESC LIMIT {int(limit)}'
    ).fetchall()
    return [{"value": r[0], "rows": int(r[1])} for r in rows]


def orphan_count(conn: sqlite3.Connection, fk: dict[str, Any]) -> int | None:
    try:
        r = conn.execute(
            f'SELECT COUNT(*) FROM {q(fk["from_table"])} a '
            f'LEFT JOIN {q(fk["to_table"])} b '
            f'ON a.{q(fk["from_column"])}=b.{q(fk["to_column"])} '
            f'WHERE a.{q(fk["from_column"])} IS NOT NULL '
            f'AND b.{q(fk["to_column"])} IS NULL'
        ).fetchone()
        return int(r[0] or 0)
    except Exception:
        return None


def same_name_join_candidates(conn: sqlite3.Connection, source: str, target: str) -> list[dict[str, Any]]:
    scols = set(colnames(conn, source))
    tcols = set(colnames(conn, target))
    common = sorted(c for c in scols & tcols if c.endswith("_id") or c in {
        "document_id", "news_id", "event_id", "cluster_id", "source_document_id",
        "raw_document_id", "observation_id",
    })
    out = []
    sn = count(conn, source)
    for c in common:
        try:
            matched = int(conn.execute(
                f'SELECT COUNT(*) FROM {q(source)} s WHERE s.{q(c)} IS NOT NULL '
                f'AND EXISTS (SELECT 1 FROM {q(target)} t WHERE t.{q(c)}=s.{q(c)})'
            ).fetchone()[0] or 0)
            out.append({
                "column": c,
                "source_non_null": nonnull_count(conn, source, c),
                "source_rows": sn,
                "matched_source_rows": matched,
                "matched_fraction_of_source_rows": matched/sn if sn else None,
            })
        except Exception as exc:
            out.append({"column": c, "error": str(exc)})
    return out


def candidate_time_lineage(conn: sqlite3.Connection, child: str, parent: str, join_col: str) -> dict[str, Any]:
    child_cols = set(colnames(conn, child))
    parent_cols = set(colnames(conn, parent))
    parent_time = next((c for c in CAUSAL_TIME_PRIORITY if c in parent_cols), None)
    if not parent_time:
        return {"status": "NO_PARENT_CAUSAL_TIME_FIELD", "join_column": join_col}
    child_n = count(conn, child)
    linked = int(conn.execute(
        f'SELECT COUNT(*) FROM {q(child)} c '
        f'JOIN {q(parent)} p ON c.{q(join_col)}=p.{q(join_col)} '
        f'WHERE p.{q(parent_time)} IS NOT NULL '
        f'AND TRIM(CAST(p.{q(parent_time)} AS TEXT))<>""'
    ).fetchone()[0] or 0)
    child_published = "published_at" if "published_at" in child_cols else None
    ordering = None
    if child_published:
        try:
            ordering = {
                "parent_time_before_or_equal_published": int(conn.execute(
                    f'SELECT COUNT(*) FROM {q(child)} c JOIN {q(parent)} p '
                    f'ON c.{q(join_col)}=p.{q(join_col)} '
                    f'WHERE p.{q(parent_time)} IS NOT NULL AND c.{q(child_published)} IS NOT NULL '
                    f'AND datetime(p.{q(parent_time)}) <= datetime(c.{q(child_published)})'
                ).fetchone()[0] or 0),
                "parent_time_after_published": int(conn.execute(
                    f'SELECT COUNT(*) FROM {q(child)} c JOIN {q(parent)} p '
                    f'ON c.{q(join_col)}=p.{q(join_col)} '
                    f'WHERE p.{q(parent_time)} IS NOT NULL AND c.{q(child_published)} IS NOT NULL '
                    f'AND datetime(p.{q(parent_time)}) > datetime(c.{q(child_published)})'
                ).fetchone()[0] or 0),
            }
        except Exception as exc:
            ordering = {"error": str(exc)}
    return {
        "status": "CANDIDATE_AVAILABLE_AT_LINEAGE",
        "join_column": join_col,
        "parent_time_field": parent_time,
        "child_rows": child_n,
        "linked_with_parent_time": linked,
        "coverage_fraction": linked/child_n if child_n else None,
        "published_vs_parent_time_diagnostic": ordering,
        "warning": (
            "This is only a lineage candidate. It does not authorize rewriting historical rows "
            "as strict PIT without verifying the acquisition semantics of the parent time."
        ),
    }


def table_profile(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    cols = columns(conn, table)
    names = [x["name"] for x in cols]
    n = count(conn, table)
    times = {
        c: minmax(conn, table, c)
        for c in names
        if c in CAUSAL_TIME_PRIORITY or c.endswith("_at") or c.endswith("_date")
    }
    ids = {}
    for c in names:
        if c.endswith("_id") or c in ("ticker","asset_ticker","symbol"):
            try:
                ids[c] = {
                    "distinct": distinct_count(conn, table, c),
                    "non_null": nonnull_count(conn, table, c),
                }
            except Exception:
                pass
    selected_categoricals = {}
    for c in names:
        low = c.lower()
        if any(k in low for k in ("source","provider","status","type","horizon","version","label","method","kind","scope")):
            try:
                d = distinct_count(conn, table, c)
                if d <= 500:
                    selected_categoricals[c] = {
                        "distinct": d,
                        "top": top_values(conn, table, c, 20),
                    }
            except Exception:
                pass
    fks = foreign_keys(conn, table)
    for fk in fks:
        fk["orphan_rows"] = orphan_count(conn, fk)
    return {
        "rows": n,
        "columns": cols,
        "time_fields": times,
        "id_fields": ids,
        "categorical_fields": selected_categoricals,
        "foreign_keys": fks,
    }


def scan_code(repo: Path, table_names: list[str]) -> dict[str, list[dict[str, Any]]]:
    results = {t: [] for t in table_names}
    skip_parts = {".git", ".venv", "venv", "__pycache__", ".pytest_cache", "reports", "data"}
    for p in repo.rglob("*"):
        if not p.is_file() or p.suffix.lower() not in CODE_SUFFIXES:
            continue
        if any(part in skip_parts for part in p.parts):
            continue
        try:
            text = p.read_text(encoding="utf-8", errors="ignore")
        except Exception:
            continue
        for table in table_names:
            if table not in text:
                continue
            lines = text.splitlines()
            hits = []
            for i, line in enumerate(lines, start=1):
                if table in line:
                    hits.append({
                        "line": i,
                        "snippet": line.strip()[:300],
                    })
                    if len(hits) >= 12:
                        break
            results[table].append({
                "file": str(p.relative_to(repo)),
                "hits": hits,
            })
    return results


def main() -> None:
    ap = argparse.ArgumentParser(description="Read-only schema, lineage and writer-code review for News/Event data.")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--db", default="data/database/market_data_v2.db")
    ap.add_argument("--output", default="reports/news_event_lineage_review_v001/summary.json")
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    db = Path(args.db)
    if not db.is_absolute():
        db = repo / db

    conn = ro_connect(db)
    present = tables(conn)
    targets = [t for t in TARGET_TABLES if t in present]
    profiles = {t: table_profile(conn, t) for t in targets}

    # Explicit schema/lineage review around the critical News/Event path.
    joins = {}
    pairs = [
        ("news_documents","raw_source_documents"),
        ("news_assets","news_documents"),
        ("event_news","news_documents"),
        ("event_cluster_news","news_documents"),
        ("event_cluster_news","event_clusters"),
        ("event_evidence","events"),
        ("event_states","events"),
        ("event_reaction_outcomes","events"),
        ("normalized_event_reaction_labels","normalized_event_observations"),
    ]
    for a,b in pairs:
        if a in present and b in present:
            joins[f"{a}->{b}"] = same_name_join_candidates(conn, a, b)

    news_time_lineage = []
    if "news_documents" in present and "raw_source_documents" in present:
        candidates = same_name_join_candidates(conn, "news_documents", "raw_source_documents")
        for c in candidates:
            if c.get("matched_source_rows", 0) > 0:
                news_time_lineage.append(candidate_time_lineage(
                    conn, "news_documents", "raw_source_documents", c["column"]
                ))

    # Cluster coverage through any membership table with a document-like id and cluster-like id.
    cluster_coverage = []
    if "news_documents" in present:
        news_cols = set(colnames(conn, "news_documents"))
        news_pk = next((x["name"] for x in columns(conn, "news_documents") if x["pk"]), None)
        for mt in ("event_cluster_news","event_cluster_news_membership_refs","event_news"):
            if mt not in present:
                continue
            mcols = colnames(conn, mt)
            doc_cols = [c for c in mcols if "document" in c.lower() or "news" in c.lower()]
            cluster_cols = [c for c in mcols if "cluster" in c.lower()]
            cluster_coverage.append({
                "membership_table": mt,
                "rows": count(conn, mt),
                "document_candidate_columns": doc_cols,
                "cluster_candidate_columns": cluster_cols,
                "news_documents_primary_key": news_pk,
            })

    reaction_tables = {}
    for t in ("event_reaction_outcomes","normalized_event_reaction_labels"):
        if t in profiles:
            reaction_tables[t] = profiles[t]

    conn.close()

    code_refs = scan_code(repo, targets)

    warnings = []
    if not news_time_lineage:
        warnings.append("NO_PROVEN_NEWS_DOCUMENT_TO_RAW_TIME_LINEAGE")
    elif max((x.get("coverage_fraction") or 0) for x in news_time_lineage) < 0.95:
        warnings.append("NEWS_TIME_LINEAGE_COVERAGE_BELOW_95_PERCENT")
    if not reaction_tables:
        warnings.append("REACTION_LABEL_TABLES_MISSING")
    if reaction_tables and not any(code_refs.get(t) for t in reaction_tables):
        warnings.append("REACTION_LABEL_WRITER_CODE_NOT_LOCATED")

    if "NO_PROVEN_NEWS_DOCUMENT_TO_RAW_TIME_LINEAGE" in warnings:
        gate = "BLOCK_PREDICTIVE_NEWS_USE"
    elif "NEWS_TIME_LINEAGE_COVERAGE_BELOW_95_PERCENT" in warnings:
        gate = "REVIEW_PARTIAL_TIME_LINEAGE"
    else:
        gate = "REVIEW_ACQUISITION_SEMANTICS_AND_LABEL_LOGIC"

    result = {
        "status": "PASS",
        "contract_version": CONTRACT_VERSION,
        "mode": "READ_ONLY",
        "database": str(db),
        "table_profiles": profiles,
        "join_diagnostics": joins,
        "news_available_at_lineage_candidates": news_time_lineage,
        "cluster_membership_schema": cluster_coverage,
        "reaction_label_profiles": reaction_tables,
        "writer_code_references": code_refs,
        "warnings": warnings,
        "gate": gate,
        "interpretation": (
            "Presence of a joinable parent timestamp is not by itself proof of strict PIT. "
            "The next decision depends on whether that timestamp represents the actual historical "
            "acquisition path and on how reaction labels were aligned to sessions and event/news times."
        ),
    }

    out = Path(args.output)
    if not out.is_absolute():
        out = repo / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    compact = {
        "status": result["status"],
        "contract_version": CONTRACT_VERSION,
        "output": str(out),
        "gate": gate,
        "warnings": warnings,
        "news_available_at_lineage_candidates": news_time_lineage,
        "cluster_membership_schema": cluster_coverage,
        "reaction_tables": {
            t: {
                "rows": info["rows"],
                "time_fields": info["time_fields"],
                "id_fields": info["id_fields"],
                "categorical_fields": info["categorical_fields"],
                "foreign_keys": info["foreign_keys"],
                "writer_files": [x["file"] for x in code_refs.get(t, [])],
            }
            for t,info in reaction_tables.items()
        },
        "news_writer_files": [x["file"] for x in code_refs.get("news_documents", [])],
        "raw_source_writer_files": [x["file"] for x in code_refs.get("raw_source_documents", [])],
    }
    print(json.dumps(compact, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any, Iterable

CONTRACT_VERSION = "information_archaeology_v001"

PRIMARY_TABLES = [
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
    "relation_evidence",
    "entity_relations",
    "relation_types",
    "graph_relation_candidates_v001",
    "temporal_relation_observations_v001",
    "temporal_relation_assertions_v001",
    "market_sessions",
    "price_bars",
    "macro_observations",
    "predictions",
    "prediction_outcomes",
    "prediction_diagnostics",
]

TIME_NAMES = (
    "available_at", "first_seen_at", "retrieved_at", "published_at", "event_time",
    "occurred_at", "observed_at", "timestamp", "ts", "date", "trade_date",
    "scheduled_at", "resolved_at", "effective_from", "effective_to", "created_at",
)

SOURCE_NAMES = (
    "source_name", "source", "publisher", "provider", "domain", "publisher_name",
    "source_type",
)

ASSET_NAMES = (
    "asset_id", "asset_ticker", "ticker", "symbol",
)

ID_HINTS = (
    "document_id", "news_document_id", "event_id", "cluster_id", "asset_id",
    "entity_id", "source_document_id", "raw_document_id", "prediction_id",
)

PIT_NAMES = ("strict_pit", "pit", "is_point_in_time", "point_in_time")


def ro_connect(path: Path) -> sqlite3.Connection:
    if not path.exists():
        raise FileNotFoundError(path)
    uri = f"file:{path.resolve().as_posix()}?mode=ro"
    c = sqlite3.connect(uri, uri=True)
    c.row_factory = sqlite3.Row
    return c


def tables(conn: sqlite3.Connection) -> list[str]:
    return [str(r[0]) for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
    )]


def columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    out = []
    for r in conn.execute(f'PRAGMA table_info("{table}")'):
        out.append({
            "cid": r[0], "name": r[1], "type": r[2], "notnull": r[3],
            "default": r[4], "pk": r[5],
        })
    return out


def row_count(conn: sqlite3.Connection, table: str) -> int:
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def scalar(conn: sqlite3.Connection, sql: str) -> Any:
    r = conn.execute(sql).fetchone()
    return None if r is None else r[0]


def q(name: str) -> str:
    return '"' + name.replace('"', '""') + '"'


def null_fraction(conn: sqlite3.Connection, table: str, col: str, n: int) -> float | None:
    if n == 0:
        return None
    missing = scalar(conn, f'SELECT COUNT(*) FROM {q(table)} WHERE {q(col)} IS NULL OR TRIM(CAST({q(col)} AS TEXT))=""')
    return float(missing) / n


def distinct_count(conn: sqlite3.Connection, table: str, col: str) -> int:
    return int(scalar(conn, f'SELECT COUNT(DISTINCT {q(col)}) FROM {q(table)} WHERE {q(col)} IS NOT NULL') or 0)


def minmax(conn: sqlite3.Connection, table: str, col: str) -> dict[str, Any]:
    r = conn.execute(
        f'SELECT MIN({q(col)}), MAX({q(col)}), '
        f'COUNT({q(col)}) FROM {q(table)}'
    ).fetchone()
    return {"min": r[0], "max": r[1], "non_null": int(r[2] or 0)}


def top_values(conn: sqlite3.Connection, table: str, col: str, limit: int = 15) -> list[dict[str, Any]]:
    rows = conn.execute(
        f'SELECT CAST({q(col)} AS TEXT) value, COUNT(*) n '
        f'FROM {q(table)} WHERE {q(col)} IS NOT NULL AND TRIM(CAST({q(col)} AS TEXT))<>"" '
        f'GROUP BY {q(col)} ORDER BY n DESC LIMIT {int(limit)}'
    ).fetchall()
    return [{"value": r[0], "rows": int(r[1])} for r in rows]


def inspect_table(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    cols = columns(conn, table)
    names = [c["name"] for c in cols]
    name_set = set(names)
    n = row_count(conn, table)

    times = {}
    for name in names:
        if name.lower() in TIME_NAMES or name.lower().endswith("_at"):
            try:
                times[name] = minmax(conn, table, name)
            except Exception as exc:
                times[name] = {"error": str(exc)}

    pit = {}
    for name in names:
        if name.lower() in PIT_NAMES:
            try:
                pit[name] = top_values(conn, table, name, 10)
            except Exception as exc:
                pit[name] = {"error": str(exc)}

    sources = {}
    for name in names:
        if name.lower() in SOURCE_NAMES:
            try:
                sources[name] = {
                    "distinct": distinct_count(conn, table, name),
                    "top": top_values(conn, table, name, 15),
                }
            except Exception as exc:
                sources[name] = {"error": str(exc)}

    assets = {}
    for name in names:
        if name.lower() in ASSET_NAMES:
            try:
                assets[name] = {
                    "distinct": distinct_count(conn, table, name),
                    "null_fraction": null_fraction(conn, table, name, n),
                }
            except Exception as exc:
                assets[name] = {"error": str(exc)}

    ids = {}
    for name in names:
        if name.lower().endswith("_id") or name.lower() in ID_HINTS:
            try:
                ids[name] = {
                    "distinct": distinct_count(conn, table, name),
                    "null_fraction": null_fraction(conn, table, name, n),
                }
            except Exception:
                pass

    key_nulls = {}
    candidates = set(TIME_NAMES) | set(SOURCE_NAMES) | set(ASSET_NAMES) | set(PIT_NAMES)
    for name in names:
        low = name.lower()
        if low in candidates or low in ("title","headline","url","canonical_url","summary","content_hash","sha256"):
            try:
                key_nulls[name] = null_fraction(conn, table, name, n)
            except Exception:
                pass

    return {
        "rows": n,
        "columns": cols,
        "time_coverage": times,
        "pit_fields": pit,
        "source_fields": sources,
        "asset_fields": assets,
        "id_fields": ids,
        "key_null_fractions": key_nulls,
    }


def fk_edges(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    out = []
    try:
        rows = conn.execute(f'PRAGMA foreign_key_list("{table}")').fetchall()
    except Exception:
        return out
    for r in rows:
        out.append({
            "from_table": table,
            "from_column": r[3],
            "to_table": r[2],
            "to_column": r[4],
        })
    return out


def orphan_count(conn: sqlite3.Connection, edge: dict[str, Any]) -> int | None:
    ft, fc, tt, tc = edge["from_table"], edge["from_column"], edge["to_table"], edge["to_column"]
    try:
        return int(scalar(
            conn,
            f'SELECT COUNT(*) FROM {q(ft)} a LEFT JOIN {q(tt)} b '
            f'ON a.{q(fc)}=b.{q(tc)} '
            f'WHERE a.{q(fc)} IS NOT NULL AND b.{q(tc)} IS NULL'
        ) or 0)
    except Exception:
        return None


def inspect_db(path: Path, selected_tables: Iterable[str] | None = None) -> dict[str, Any]:
    c = ro_connect(path)
    ts = tables(c)
    selected = [t for t in (selected_tables or ts) if t in ts]
    table_info = {}
    edges = []
    for t in selected:
        table_info[t] = inspect_table(c, t)
        edges.extend(fk_edges(c, t))
    for edge in edges:
        edge["orphan_rows"] = orphan_count(c, edge)
    c.close()
    return {
        "path": str(path),
        "size_bytes": path.stat().st_size,
        "tables_total": len(ts),
        "selected_tables_present": selected,
        "selected_tables_missing": [t for t in (selected_tables or []) if t not in ts],
        "table_info": table_info,
        "foreign_key_edges": edges,
    }


def semantic_summary(primary: dict[str, Any], relation_dbs: list[dict[str, Any]]) -> dict[str, Any]:
    ti = primary["table_info"]

    def rows(table: str) -> int:
        return int(ti.get(table, {}).get("rows", 0))

    news_rows = rows("news_documents")
    raw_rows = rows("raw_source_documents")
    event_rows = rows("events") + rows("normalized_event_observations")
    cluster_rows = rows("event_clusters")
    reaction_rows = rows("event_reaction_outcomes") + rows("normalized_event_reaction_labels")
    graph_rows = rows("relation_evidence") + rows("entity_relations") + rows("graph_relation_candidates_v001")

    temporal_tables = []
    available_at_tables = []
    pit_tables = []
    for table, info in ti.items():
        times = info.get("time_coverage", {})
        if times:
            temporal_tables.append(table)
        if "available_at" in times:
            available_at_tables.append(table)
        if info.get("pit_fields"):
            pit_tables.append(table)

    relation_total_rows = 0
    for db in relation_dbs:
        for info in db.get("table_info", {}).values():
            relation_total_rows += int(info.get("rows", 0))

    warnings = []
    if news_rows and not rows("news_assets"):
        warnings.append("NEWS_DOCUMENTS_WITHOUT_NEWS_ASSET_LAYER")
    if news_rows and not cluster_rows:
        warnings.append("NEWS_PRESENT_BUT_EVENT_CLUSTER_LAYER_EMPTY")
    if event_rows and not reaction_rows:
        warnings.append("EVENTS_PRESENT_BUT_REACTION_LABEL_LAYER_EMPTY")
    if news_rows and "news_documents" not in available_at_tables:
        warnings.append("NEWS_DOCUMENTS_LACK_AVAILABLE_AT_FIELD")
    if graph_rows == 0 and relation_total_rows == 0:
        warnings.append("NO_POPULATED_RELATION_FOUNDATION_DETECTED")

    return {
        "news_documents_rows": news_rows,
        "raw_source_documents_rows": raw_rows,
        "event_or_normalized_event_rows": event_rows,
        "event_cluster_rows": cluster_rows,
        "event_reaction_or_label_rows": reaction_rows,
        "primary_graph_rows": graph_rows,
        "processed_relation_db_rows_total": relation_total_rows,
        "tables_with_any_time_field": sorted(temporal_tables),
        "tables_with_available_at": sorted(available_at_tables),
        "tables_with_explicit_pit_field": sorted(pit_tables),
        "warnings": warnings,
        "next_gate": (
            "SCHEMA_AND_LINEAGE_REVIEW"
            if warnings else
            "TEMPORAL_AND_SEMANTIC_QUALITY_REVIEW"
        ),
    }


def discover_relation_dbs(root: Path) -> list[Path]:
    patterns = [
        "data/processed/event_graph*.db",
        "data/processed/*relation*.db",
        "data/processed/*identity*.db",
    ]
    found = set()
    for pattern in patterns:
        for p in root.glob(pattern):
            if p.is_file():
                found.add(p.resolve())
    return sorted(found)


def main() -> None:
    ap = argparse.ArgumentParser(description="Read-only archaeology audit of News/Event/Graph data foundations.")
    ap.add_argument("--primary-db", default="data/database/market_data_v2.db")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--output", default="reports/information_archaeology_v001/summary.json")
    ap.add_argument("--skip-relation-dbs", action="store_true")
    args = ap.parse_args()

    repo = Path(args.repo_root).resolve()
    primary_path = Path(args.primary_db)
    if not primary_path.is_absolute():
        primary_path = repo / primary_path

    primary = inspect_db(primary_path, PRIMARY_TABLES)

    relation_reports = []
    if not args.skip_relation_dbs:
        for p in discover_relation_dbs(repo):
            try:
                relation_reports.append(inspect_db(p))
            except Exception as exc:
                relation_reports.append({"path": str(p), "error": str(exc)})

    result = {
        "status": "PASS",
        "contract_version": CONTRACT_VERSION,
        "mode": "READ_ONLY",
        "primary": primary,
        "processed_relation_databases": relation_reports,
    }
    result["semantic_summary"] = semantic_summary(primary, relation_reports)

    out = Path(args.output)
    if not out.is_absolute():
        out = repo / out
    out.parent.mkdir(parents=True, exist_ok=True)
    out.write_text(json.dumps(result, indent=2, ensure_ascii=False, sort_keys=True) + "\n", encoding="utf-8")

    # Keep console compact. The complete evidence is in the JSON.
    compact = {
        "status": result["status"],
        "contract_version": CONTRACT_VERSION,
        "output": str(out),
        "primary_db": str(primary_path),
        "primary_db_size_gib": round(primary_path.stat().st_size / (1024**3), 3),
        "selected_tables_present": primary["selected_tables_present"],
        "semantic_summary": result["semantic_summary"],
        "relation_db_count": len(relation_reports),
        "relation_dbs": [
            {
                "path": x.get("path"),
                "tables_total": x.get("tables_total"),
                "size_mib": None if "size_bytes" not in x else round(x["size_bytes"]/(1024**2), 2),
                "error": x.get("error"),
            }
            for x in relation_reports
        ],
    }
    print(json.dumps(compact, indent=2, ensure_ascii=False, sort_keys=True))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import hashlib
import json
import math
import os
import sqlite3
import sys
from collections import Counter
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping, Sequence


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "information_integration_readiness_v001.json"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "information_integration_readiness_v001"
CONTRACT_VERSION = "information_integration_readiness_v001"

REQUIRED_TABLES = {
    "reference_state": {"market_daily_v003_states", "build_metadata"},
    "market_source": {
        "assets",
        "ingestion_sources",
        "price_bar_versions",
        "price_bar_observations",
        "price_bars",
        "market_state_v002_snapshots",
        "news_documents",
        "news_assets",
        "raw_source_documents",
        "sec_filings",
        "normalized_event_observations",
        "normalized_event_state_snapshots",
        "macro_observations",
    },
    "external_market": {"market_external_state_v005"},
    "financial_conditions": {"market_financial_conditions_v0052"},
    "historical_event_dataset": {
        "samples",
        "sample_events",
        "sample_groups",
        "outcomes",
    },
    "prospective_information": {
        "source_observations",
        "expectation_observations",
        "scheduled_event_window_observations",
        "news_document_observations",
        "economic_fact_observations",
    },
    "graph_entity_evidence": {
        "registry_runs",
        "identity_evidence_buckets",
        "identity_alias_evidence",
    },
    "graph_relation_evidence": {
        "extraction_runs",
        "evidence_claims",
        "contract_party_sets",
    },
}

REPORT_FILENAMES = {
    "inventory": "inventory_report.json",
    "coverage": "coverage_matrix.json",
    "readiness": "feature_readiness_report.json",
    "gaps": "gap_plan.json",
    "plan": "plan.json",
    "summary": "INFORMATION_INVENTORY.md",
    "audit": "audit.json",
}


def qident(value: str) -> str:
    return '"' + str(value).replace('"', '""') + '"'


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def json_bytes(payload: Any) -> bytes:
    return (
        json.dumps(payload, indent=2, ensure_ascii=False, sort_keys=True) + "\n"
    ).encode("utf-8")


def atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    temporary = path.with_name(f".{path.name}.tmp-{os.getpid()}")
    temporary.write_bytes(payload)
    os.replace(temporary, path)


def file_state(path: Path) -> dict[str, Any]:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        return {"path": str(resolved), "exists": False}
    stat = resolved.stat()
    sidecars: dict[str, Any] = {}
    for suffix in ("-wal", "-shm", "-journal"):
        candidate = Path(str(resolved) + suffix)
        if candidate.exists():
            s = candidate.stat()
            sidecars[suffix] = {"size_bytes": s.st_size, "mtime_ns": s.st_mtime_ns}
    return {
        "path": str(resolved),
        "exists": True,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
        "sidecars": sidecars,
    }


def connect_read_only(path: Path, opened_paths: list[str]) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    if not resolved.exists():
        raise FileNotFoundError(resolved)
    conn = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    opened_paths.append(str(resolved))
    return conn


def table_names(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type IN ('table','view')"
        )
    }


def table_columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [str(row["name"]) for row in conn.execute(f"PRAGMA table_info({qident(table)})")]


def rows_as_dict(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> list[dict[str, Any]]:
    return [dict(row) for row in conn.execute(sql, tuple(params)).fetchall()]


def row_as_dict(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> dict[str, Any]:
    row = conn.execute(sql, tuple(params)).fetchone()
    return {} if row is None else dict(row)


def scalar(conn: sqlite3.Connection, sql: str, params: Sequence[Any] = ()) -> Any:
    row = conn.execute(sql, tuple(params)).fetchone()
    return None if row is None else row[0]


def distribution_summary(values: Iterable[int | float]) -> dict[str, Any]:
    ordered = sorted(float(value) for value in values)
    if not ordered:
        return {"count": 0, "min": None, "p25": None, "median": None, "p75": None, "max": None}

    def quantile(p: float) -> float:
        if len(ordered) == 1:
            return ordered[0]
        position = p * (len(ordered) - 1)
        lower = int(math.floor(position))
        upper = int(math.ceil(position))
        if lower == upper:
            return ordered[lower]
        weight = position - lower
        return ordered[lower] * (1.0 - weight) + ordered[upper] * weight

    output = {
        "count": len(ordered),
        "min": ordered[0],
        "p25": quantile(0.25),
        "median": quantile(0.50),
        "p75": quantile(0.75),
        "max": ordered[-1],
    }
    for key, value in list(output.items()):
        if key != "count" and isinstance(value, float) and value.is_integer():
            output[key] = int(value)
    return output


def null_counts(conn: sqlite3.Connection, table: str, columns: Sequence[str]) -> dict[str, int]:
    if not columns:
        return {}
    expressions = [
        f"SUM(CASE WHEN {qident(column)} IS NULL THEN 1 ELSE 0 END) AS {qident(column)}"
        for column in columns
    ]
    row = conn.execute(f"SELECT {','.join(expressions)} FROM {qident(table)}").fetchone()
    return {column: int(row[column] or 0) for column in columns}


def resolve_path(repo_root: Path, value: str) -> Path:
    path = Path(value)
    return path.resolve() if path.is_absolute() else (repo_root / path).resolve()


def validate_config(config: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if config.get("version") != CONTRACT_VERSION:
        errors.append(f"version must equal {CONTRACT_VERSION}")

    guards = config.get("guards", {})
    for key in (
        "training_authorized",
        "materialization_authorized_by_this_audit",
        "source_database_mutation_allowed",
        "outcome_columns_feature_eligible",
        "v009_access_allowed",
    ):
        if guards.get(key) is not False:
            errors.append(f"guards.{key} must be false")

    sources = config.get("sources", {})
    expected_sources = set(REQUIRED_TABLES) - {"reference_state"}
    missing_sources = sorted(expected_sources - set(sources))
    if missing_sources:
        errors.append(f"missing configured sources: {missing_sources}")

    forbidden_path_tokens = [str(value).lower() for value in guards.get("forbidden_source_path_tokens", [])]
    configured_paths = [str(config.get("reference_state", {}).get("database", ""))]
    configured_paths.extend(str(value.get("database", "")) for value in sources.values())
    forbidden_path_hits: list[dict[str, str]] = []
    for path in configured_paths:
        lowered = path.lower()
        for token in forbidden_path_tokens:
            if token and token in lowered:
                forbidden_path_hits.append({"path": path, "token": token})
    if forbidden_path_hits:
        errors.append(f"forbidden source paths configured: {forbidden_path_hits}")

    feature_blocks = config.get("feature_blocks", {})
    all_features: list[str] = []
    for block_name, block in feature_blocks.items():
        features = block.get("features", [])
        if not features:
            errors.append(f"feature block {block_name} is empty")
        all_features.extend(str(feature) for feature in features)
    duplicates = sorted(name for name, count in Counter(all_features).items() if count > 1)
    if duplicates:
        errors.append(f"features occur in multiple blocks: {duplicates}")

    forbidden_name_tokens = [str(value).lower() for value in guards.get("forbidden_feature_name_tokens", [])]
    forbidden_exact = {str(value).lower() for value in guards.get("forbidden_feature_exact_names", [])}
    leakage_hits: list[str] = []
    for feature in all_features:
        lowered = feature.lower()
        if lowered in forbidden_exact or any(token and token in lowered for token in forbidden_name_tokens):
            leakage_hits.append(feature)
    if leakage_hits:
        errors.append(f"outcome/future-like feature names are forbidden: {sorted(leakage_hits)}")

    return {
        "valid": not errors,
        "errors": errors,
        "configured_source_paths": configured_paths,
        "forbidden_path_hits": forbidden_path_hits,
        "declared_feature_count": len(all_features),
        "declared_feature_duplicates": duplicates,
        "feature_leakage_hits": sorted(leakage_hits),
    }


def inspect_schema(
    path: Path,
    logical_name: str,
    opened_paths: list[str],
) -> dict[str, Any]:
    if not path.exists():
        return {
            "logical_name": logical_name,
            "path": str(path),
            "exists": False,
            "opened_read_only": False,
            "required_tables": sorted(REQUIRED_TABLES[logical_name]),
            "missing_required_tables": sorted(REQUIRED_TABLES[logical_name]),
        }
    with closing(connect_read_only(path, opened_paths)) as conn:
        present = table_names(conn)
        required = REQUIRED_TABLES[logical_name]
        return {
            "logical_name": logical_name,
            "path": str(path),
            "exists": True,
            "opened_read_only": True,
            "table_count": len(present),
            "required_tables": sorted(required),
            "missing_required_tables": sorted(required - present),
        }


def profile_core(
    path: Path,
    config: Mapping[str, Any],
    opened_paths: list[str],
) -> tuple[dict[str, Any], dict[str, Any]]:
    table = str(config["reference_state"]["table"])
    with closing(connect_read_only(path, opened_paths)) as conn:
        columns = set(table_columns(conn, table))
        summary = row_as_dict(
            conn,
            f"""
            SELECT COUNT(*) AS states,
                   COUNT(DISTINCT asset_id) AS assets,
                   COUNT(DISTINCT ticker) AS tickers,
                   COUNT(DISTINCT trading_day) AS origin_days,
                   MIN(trading_day) AS min_day,
                   MAX(trading_day) AS max_day,
                   MIN(state_time) AS min_state_time,
                   MAX(state_time) AS max_state_time,
                   SUM(CASE WHEN state_point_in_time_verified=1 THEN 1 ELSE 0 END) AS strict_pit_rows
            FROM {qident(table)}
            """,
        )
        duplicate_state_ids = int(
            scalar(
                conn,
                f"SELECT COUNT(*) FROM (SELECT state_id FROM {qident(table)} GROUP BY state_id HAVING COUNT(*)>1)",
            )
            or 0
        )
        duplicate_asset_days = int(
            scalar(
                conn,
                f"SELECT COUNT(*) FROM (SELECT asset_id,trading_day FROM {qident(table)} GROUP BY asset_id,trading_day HAVING COUNT(*)>1)",
            )
            or 0
        )
        feature_versions = rows_as_dict(
            conn,
            f"SELECT feature_version,COUNT(*) AS rows FROM {qident(table)} GROUP BY feature_version ORDER BY rows DESC",
        )
        sectors = rows_as_dict(
            conn,
            f"SELECT COALESCE(sector,'<NULL>') AS sector,COUNT(*) AS states,COUNT(DISTINCT asset_id) AS assets FROM {qident(table)} GROUP BY sector ORDER BY states DESC",
        )
        rows_per_asset = [
            int(row[0])
            for row in conn.execute(
                f"SELECT COUNT(*) FROM {qident(table)} GROUP BY asset_id"
            )
        ]
        asset_coverage = rows_as_dict(
            conn,
            f"""
            SELECT asset_id,ticker,sector,COUNT(*) AS states,
                   MIN(trading_day) AS min_day,MAX(trading_day) AS max_day
            FROM {qident(table)}
            GROUP BY asset_id,ticker,sector ORDER BY ticker
            """,
        )
        day_counts = {
            str(row["trading_day"]): int(row["states"])
            for row in conn.execute(
                f"SELECT trading_day,COUNT(*) AS states FROM {qident(table)} GROUP BY trading_day"
            )
        }
        core_tickers = {
            str(row[0])
            for row in conn.execute(f"SELECT DISTINCT ticker FROM {qident(table)}")
        }
        core_assets = {
            int(row[0])
            for row in conn.execute(f"SELECT DISTINCT asset_id FROM {qident(table)}")
        }
        asset_id_to_ticker = {
            int(row[0]): str(row[1])
            for row in conn.execute(
                f"SELECT DISTINCT asset_id,ticker FROM {qident(table)}"
            )
        }

        block_reports: dict[str, Any] = {}
        declared_features: set[str] = set()
        for block_name, block in config["feature_blocks"].items():
            if block.get("source") != "reference_state":
                continue
            features = [str(value) for value in block["features"]]
            declared_features.update(features)
            missing = sorted(set(features) - columns)
            present = [feature for feature in features if feature in columns]
            block_reports[block_name] = {
                "declared_features": features,
                "feature_count": len(features),
                "missing_columns": missing,
                "null_counts": null_counts(conn, table, present),
                "eligibility": block.get("eligibility"),
            }

        metadata_rows = rows_as_dict(conn, "SELECT key,value_json FROM build_metadata ORDER BY key")
        metadata: dict[str, Any] = {}
        for row in metadata_rows:
            try:
                metadata[str(row["key"])] = json.loads(str(row["value_json"]))
            except json.JSONDecodeError:
                metadata[str(row["key"])] = row["value_json"]

    expected_version = str(config["reference_state"]["feature_version"])
    observed_versions = {str(row["feature_version"]) for row in feature_versions}
    profile = {
        "layer": "market_core_state",
        "unit": "asset_origin_session_close",
        "summary": summary,
        "duplicate_state_ids": duplicate_state_ids,
        "duplicate_asset_days": duplicate_asset_days,
        "feature_versions": feature_versions,
        "expected_feature_version": expected_version,
        "feature_version_matches": observed_versions == {expected_version},
        "rows_per_asset": distribution_summary(rows_per_asset),
        "asset_coverage": asset_coverage,
        "sectors": sectors,
        "feature_blocks": block_reports,
        "build_metadata": metadata,
        "historical_semantics": "historical_session_close_assumption_strict_pit_false",
        "feature_eligibility": "READY_FOR_HISTORICAL_RESEARCH_BASELINE_AND_INCREMENT_TESTS",
    }
    internal = {
        "day_counts": day_counts,
        "tickers": core_tickers,
        "asset_ids": core_assets,
        "asset_id_to_ticker": asset_id_to_ticker,
        "max_day": summary.get("max_day"),
        "max_state_time": summary.get("max_state_time"),
        "states": int(summary.get("states") or 0),
        "assets": int(summary.get("assets") or 0),
        "days": int(summary.get("origin_days") or 0),
        "declared_core_features": declared_features,
    }
    return profile, internal


def profile_day_context(
    path: Path,
    logical_name: str,
    source_config: Mapping[str, Any],
    block_name: str,
    config: Mapping[str, Any],
    core: Mapping[str, Any],
    opened_paths: list[str],
) -> dict[str, Any]:
    table = str(source_config["table"])
    day_column = str(source_config["join_key"])
    features = [str(value) for value in config["feature_blocks"][block_name]["features"]]
    core_days = set(core["day_counts"])
    with closing(connect_read_only(path, opened_paths)) as conn:
        columns = set(table_columns(conn, table))
        missing = sorted(set(features) - columns)
        present_features = [feature for feature in features if feature in columns]
        summary = row_as_dict(
            conn,
            f"SELECT COUNT(*) AS rows,COUNT(DISTINCT {qident(day_column)}) AS days,MIN({qident(day_column)}) AS min_day,MAX({qident(day_column)}) AS max_day FROM {qident(table)}",
        )
        duplicate_days = int(
            scalar(
                conn,
                f"SELECT COUNT(*) FROM (SELECT {qident(day_column)} FROM {qident(table)} GROUP BY {qident(day_column)} HAVING COUNT(*)>1)",
            )
            or 0
        )
        context_days = {
            str(row[0])
            for row in conn.execute(f"SELECT {qident(day_column)} FROM {qident(table)}")
        }
        version_column = "feature_version" if "feature_version" in columns else None
        feature_versions = (
            rows_as_dict(
                conn,
                f"SELECT feature_version,COUNT(*) AS rows FROM {qident(table)} GROUP BY feature_version ORDER BY rows DESC",
            )
            if version_column
            else []
        )
        semantic_columns = [
            column
            for column in (
                "point_in_time_verified",
                "historical_strict_pit",
                "availability_basis",
                "price_observation_policy",
                "action_observation_policy",
                "return_convention",
                "cash_action_availability_basis",
                "vix_feature_lag_sessions",
                "adjusted_close_used",
            )
            if column in columns
        ]
        semantics: dict[str, Any] = {}
        for column in semantic_columns:
            semantics[column] = rows_as_dict(
                conn,
                f"SELECT {qident(column)} AS value,COUNT(*) AS rows FROM {qident(table)} GROUP BY {qident(column)} ORDER BY rows DESC",
            )
        feature_nulls = null_counts(conn, table, present_features)
        core_join_feature_nulls = {feature: 0 for feature in present_features}
        selected_columns = [day_column, *present_features]
        for row in conn.execute(
            f"SELECT {','.join(qident(column) for column in selected_columns)} FROM {qident(table)}"
        ):
            if str(row[day_column]) not in core_days:
                continue
            for feature in present_features:
                if row[feature] is None:
                    core_join_feature_nulls[feature] += 1

    matched_days = core_days & context_days
    unmatched_days = sorted(core_days - context_days)
    matched_states = sum(int(core["day_counts"][day]) for day in matched_days)
    total_states = int(core["states"])
    return {
        "layer": logical_name,
        "unit": "origin_trading_day",
        "join_key": day_column,
        "summary": summary,
        "duplicate_join_days": duplicate_days,
        "feature_versions": feature_versions,
        "declared_features": features,
        "feature_count": len(features),
        "missing_feature_columns": missing,
        "feature_null_counts": feature_nulls,
        "core_join_feature_null_counts": core_join_feature_nulls,
        "semantics": semantics,
        "core_join_coverage": {
            "core_days": len(core_days),
            "matched_core_days": len(matched_days),
            "unmatched_core_days": len(unmatched_days),
            "unmatched_core_day_examples": unmatched_days[:20],
            "core_states": total_states,
            "matched_core_states": matched_states,
            "matched_state_fraction": (matched_states / total_states) if total_states else None,
            "context_days_outside_core": len(context_days - core_days),
        },
        "historical_semantics": "historical_reconstruction_strict_pit_false",
        "feature_eligibility": "READY_FOR_SEPARATE_INCREMENT_TEST_NOT_AUTOMATIC_STACKING",
    }


def profile_market_source(
    path: Path,
    core: Mapping[str, Any],
    opened_paths: list[str],
) -> dict[str, Any]:
    with closing(connect_read_only(path, opened_paths)) as conn:
        asset_summary = row_as_dict(
            conn,
            """
            SELECT COUNT(*) AS assets,
                   SUM(CASE WHEN active=1 THEN 1 ELSE 0 END) AS active_assets,
                   COUNT(DISTINCT sector) AS sectors,
                   COUNT(DISTINCT exchange) AS exchanges,
                   COUNT(DISTINCT country) AS countries
            FROM assets
            """,
        )
        asset_sources = rows_as_dict(
            conn,
            "SELECT source,asset_type,COUNT(*) AS assets FROM assets GROUP BY source,asset_type ORDER BY assets DESC",
        )
        asset_catalog = rows_as_dict(
            conn,
            """
            WITH daily AS (
                SELECT asset_id,COUNT(DISTINCT trading_day) AS daily_sessions,
                       MIN(trading_day) AS min_daily_day,MAX(trading_day) AS max_daily_day
                FROM price_bar_versions GROUP BY asset_id
            )
            SELECT a.asset_id,a.ticker,a.name,a.asset_type,a.sector,a.industry,
                   a.country,a.currency,a.exchange,a.active,a.source,
                   COALESCE(d.daily_sessions,0) AS daily_sessions,
                   d.min_daily_day,d.max_daily_day
            FROM assets a LEFT JOIN daily d USING(asset_id)
            ORDER BY a.ticker
            """,
        )
        daily = row_as_dict(
            conn,
            """
            SELECT COUNT(*) AS versions,
                   COUNT(DISTINCT asset_id) AS assets,
                   COUNT(DISTINCT CAST(asset_id AS TEXT)||'|'||trading_day) AS asset_days,
                   MIN(trading_day) AS min_day,
                   MAX(trading_day) AS max_day,
                   SUM(CASE WHEN open IS NULL THEN 1 ELSE 0 END) AS missing_open,
                   SUM(CASE WHEN high IS NULL THEN 1 ELSE 0 END) AS missing_high,
                   SUM(CASE WHEN low IS NULL THEN 1 ELSE 0 END) AS missing_low,
                   SUM(CASE WHEN close IS NULL THEN 1 ELSE 0 END) AS missing_close,
                   SUM(CASE WHEN volume IS NULL THEN 1 ELSE 0 END) AS missing_volume,
                   SUM(CASE WHEN adjusted_close IS NULL THEN 1 ELSE 0 END) AS missing_adjusted_close
            FROM price_bar_versions
            """,
        )
        daily_sources = rows_as_dict(
            conn,
            """
            SELECT s.source_name,s.source_type,v.interval,COUNT(*) AS versions,
                   COUNT(DISTINCT v.asset_id) AS assets,MIN(v.trading_day) AS min_day,
                   MAX(v.trading_day) AS max_day
            FROM price_bar_versions v JOIN ingestion_sources s USING(source_id)
            GROUP BY s.source_name,s.source_type,v.interval ORDER BY versions DESC
            """,
        )
        daily_observation_semantics = rows_as_dict(
            conn,
            """
            SELECT availability_basis,point_in_time_verified,COUNT(*) AS rows,
                   COUNT(DISTINCT asset_id) AS assets,MIN(trading_day) AS min_day,
                   MAX(trading_day) AS max_day
            FROM price_bar_observations
            GROUP BY availability_basis,point_in_time_verified ORDER BY rows DESC
            """,
        )
        daily_rows_per_asset = [
            int(row[0])
            for row in conn.execute("SELECT COUNT(DISTINCT trading_day) FROM price_bar_versions GROUP BY asset_id")
        ]
        intraday_bars = rows_as_dict(
            conn,
            """
            SELECT interval,source,COUNT(*) AS bars,COUNT(DISTINCT asset_id) AS assets,
                   COUNT(DISTINCT trading_day) AS trading_days,
                   MIN(COALESCE(trading_day,timestamp)) AS min_time,
                   MAX(COALESCE(trading_day,timestamp)) AS max_time
            FROM price_bars GROUP BY interval,source ORDER BY bars DESC
            """,
        )
        intraday_states = rows_as_dict(
            conn,
            """
            SELECT feature_version,COUNT(*) AS states,COUNT(DISTINCT asset_id) AS assets,
                   COUNT(DISTINCT timestamp) AS timestamps,MIN(timestamp) AS min_time,
                   MAX(timestamp) AS max_time
            FROM market_state_v002_snapshots GROUP BY feature_version ORDER BY states DESC
            """,
        )

        news_overall = row_as_dict(
            conn,
            """
            SELECT COUNT(*) AS documents,COUNT(DISTINCT source_name) AS sources,
                   MIN(published_at) AS min_published,MAX(published_at) AS max_published,
                   MIN(ingested_at) AS min_ingested,MAX(ingested_at) AS max_ingested,
                   SUM(CASE WHEN published_at IS NULL THEN 1 ELSE 0 END) AS missing_published,
                   SUM(CASE WHEN title IS NULL OR TRIM(title)='' THEN 1 ELSE 0 END) AS missing_title,
                   SUM(CASE WHEN summary IS NULL OR TRIM(summary)='' THEN 1 ELSE 0 END) AS missing_summary,
                   SUM(CASE WHEN raw_text IS NULL OR TRIM(raw_text)='' THEN 1 ELSE 0 END) AS missing_raw_text,
                   SUM(CASE WHEN source_provider IS NULL OR TRIM(source_provider)='' THEN 1 ELSE 0 END) AS missing_source_provider
            FROM news_documents
            """,
        )
        news_categories = rows_as_dict(
            conn,
            """
            SELECT CASE WHEN source_name='SEC EDGAR' THEN 'sec_metadata_document'
                        ELSE 'media_or_other' END AS category,
                   COUNT(*) AS documents,COUNT(DISTINCT source_name) AS sources,
                   MIN(published_at) AS min_published,MAX(published_at) AS max_published,
                   MIN(ingested_at) AS min_ingested,MAX(ingested_at) AS max_ingested,
                   SUM(CASE WHEN raw_text IS NULL OR TRIM(raw_text)='' THEN 1 ELSE 0 END) AS missing_raw_text
            FROM news_documents GROUP BY category ORDER BY documents DESC
            """,
        )
        news_sources = rows_as_dict(
            conn,
            """
            SELECT source_name,COUNT(*) AS documents,MIN(published_at) AS min_published,
                   MAX(published_at) AS max_published
            FROM news_documents GROUP BY source_name ORDER BY documents DESC
            """,
        )
        news_by_asset = rows_as_dict(
            conn,
            """
            SELECT a.asset_id,a.ticker,COUNT(DISTINCT nd.news_id) AS documents,
                   COUNT(DISTINCT CASE WHEN nd.source_name='SEC EDGAR' THEN nd.news_id END) AS sec_documents,
                   COUNT(DISTINCT CASE WHEN nd.source_name<>'SEC EDGAR' THEN nd.news_id END) AS media_or_other_documents,
                   MIN(nd.published_at) AS min_published,MAX(nd.published_at) AS max_published
            FROM news_assets na
            JOIN news_documents nd USING(news_id)
            JOIN assets a USING(asset_id)
            GROUP BY a.asset_id,a.ticker ORDER BY a.ticker
            """,
        )
        news_links = row_as_dict(
            conn,
            "SELECT COUNT(*) AS links,COUNT(DISTINCT news_id) AS linked_documents,COUNT(DISTINCT asset_id) AS linked_assets FROM news_assets",
        )
        media_summary = row_as_dict(
            conn,
            """
            SELECT COUNT(*) AS documents,COUNT(DISTINCT nd.source_name) AS sources,
                   COUNT(DISTINCT na.asset_id) AS linked_assets,
                   MIN(nd.published_at) AS min_published,MAX(nd.published_at) AS max_published,
                   MIN(nd.ingested_at) AS min_ingested,MAX(nd.ingested_at) AS max_ingested
            FROM news_documents nd LEFT JOIN news_assets na USING(news_id)
            WHERE nd.source_name<>'SEC EDGAR'
            """,
        )
        news_derivatives = {
            table: int(scalar(conn, f"SELECT COUNT(*) FROM {qident(table)}") or 0)
            for table in ("news_features", "event_news", "event_cluster_news", "event_clusters")
            if table in table_names(conn)
        }

        raw_sec = row_as_dict(
            conn,
            """
            SELECT COUNT(*) AS raw_documents,MIN(r.published_at) AS min_published,
                   MAX(r.published_at) AS max_published,MIN(r.available_at) AS min_available,
                   MAX(r.available_at) AS max_available,MIN(r.retrieved_at) AS min_retrieved,
                   MAX(r.retrieved_at) AS max_retrieved,
                   SUM(CASE WHEN r.parser_status='parsed' THEN 1 ELSE 0 END) AS parsed_documents
            FROM raw_source_documents r JOIN ingestion_sources s USING(source_id)
            WHERE s.source_name='SEC EDGAR'
            """,
        )
        sec_filings = row_as_dict(
            conn,
            """
            SELECT COUNT(*) AS filings,COUNT(DISTINCT ticker_at_ingestion) AS tickers,
                   COUNT(DISTINCT cik) AS ciks,MIN(acceptance_datetime) AS min_accepted,
                   MAX(acceptance_datetime) AS max_accepted
            FROM sec_filings
            """,
        )
        sec_forms = rows_as_dict(
            conn,
            """
            SELECT form,COUNT(*) AS filings,COUNT(DISTINCT ticker_at_ingestion) AS tickers,
                   MIN(acceptance_datetime) AS min_accepted,MAX(acceptance_datetime) AS max_accepted
            FROM sec_filings GROUP BY form ORDER BY filings DESC
            """,
        )
        event_states = row_as_dict(
            conn,
            """
            SELECT COUNT(*) AS states,COUNT(DISTINCT event_id) AS events,
                   COUNT(DISTINCT asset_id) AS assets,MIN(state_time) AS min_state_time,
                   MAX(state_time) AS max_state_time,
                   SUM(CASE WHEN point_in_time_evidence_fraction=1.0 THEN 1 ELSE 0 END) AS all_evidence_pit_states
            FROM normalized_event_state_snapshots
            WHERE feature_version='event_state_v0031_deep'
            """,
        )
        event_types = rows_as_dict(
            conn,
            """
            SELECT event_type,COUNT(*) AS states,COUNT(DISTINCT event_id) AS events
            FROM normalized_event_state_snapshots
            WHERE feature_version='event_state_v0031_deep'
            GROUP BY event_type ORDER BY states DESC
            """,
        )
        event_observation_pit = rows_as_dict(
            conn,
            """
            SELECT availability_is_point_in_time AS strict_pit,COUNT(*) AS observations,
                   MIN(available_at) AS min_available,MAX(available_at) AS max_available
            FROM normalized_event_observations
            GROUP BY availability_is_point_in_time ORDER BY observations DESC
            """,
        )
        macro = rows_as_dict(
            conn,
            """
            SELECT symbol,source,COUNT(*) AS rows,MIN(observation_time) AS min_time,
                   MAX(observation_time) AS max_time
            FROM macro_observations GROUP BY symbol,source ORDER BY symbol
            """,
        )

    return {
        "layer": "market_source_inventory",
        "assets": {
            "summary": asset_summary,
            "by_source_and_type": asset_sources,
            "catalog": asset_catalog,
        },
        "daily_ohlcv": {
            "summary": daily,
            "sources": daily_sources,
            "observation_semantics": daily_observation_semantics,
            "sessions_per_asset": distribution_summary(daily_rows_per_asset),
            "fields": ["open", "high", "low", "close", "volume"],
            "adjusted_close_role": "audit_only_not_target_or_feature_without_separate_contract",
            "core_reference_assets": int(core["assets"]),
        },
        "intraday_legacy": {
            "bars": intraday_bars,
            "states": intraday_states,
            "clock_limitation": "legacy tables lack the complete modern available_at/strict-PIT state contract",
            "feature_eligibility": "PILOT_ONLY_INSUFFICIENT_TEMPORAL_DEPTH",
        },
        "legacy_news": {
            "summary": news_overall,
            "categories": news_categories,
            "top_sources": news_sources[:20],
            "all_sources": news_sources,
            "asset_links": news_links,
            "document_coverage_by_asset": news_by_asset,
            "media_or_other": media_summary,
            "derived_layers": news_derivatives,
            "available_at_column_present": False,
            "causal_interpretation": "historical_bulk_ingest_not_strict_pit; published_at is not retrieval availability",
            "feature_eligibility": "NOT_MODEL_READY_HISTORICAL_BACKFILL",
        },
        "sec_event_reconstruction": {
            "raw_documents": raw_sec,
            "filings": sec_filings,
            "forms": sec_forms,
            "deep_event_states": event_states,
            "event_types": event_types,
            "observation_pit": event_observation_pit,
            "causal_interpretation": "historical reconstruction with later retrieval; strict PIT false",
            "feature_eligibility": "SPARSE_RESEARCH_EVENT_STATE_ONLY",
        },
        "legacy_macro": {
            "observations": macro,
            "feature_eligibility": "INSUFFICIENT_HISTORY_AND_NO_RELEASE_VINTAGE_CONTRACT",
        },
    }


def profile_event_dataset(
    path: Path,
    opened_paths: list[str],
) -> dict[str, Any]:
    with closing(connect_read_only(path, opened_paths)) as conn:
        summary = row_as_dict(
            conn,
            """
            SELECT COUNT(*) AS samples,COUNT(DISTINCT asset_id) AS assets,
                   COUNT(DISTINCT ticker) AS tickers,COUNT(DISTINCT origin_day) AS origin_days,
                   MIN(origin_day) AS min_day,MAX(origin_day) AS max_day,
                   SUM(CASE WHEN strict_pit=1 THEN 1 ELSE 0 END) AS strict_pit_rows
            FROM samples
            """,
        )
        by_asset = rows_as_dict(
            conn,
            """
            SELECT ticker,COUNT(*) AS samples,COUNT(DISTINCT origin_day) AS origin_days,
                   MIN(origin_day) AS min_day,MAX(origin_day) AS max_day
            FROM samples GROUP BY ticker ORDER BY samples DESC
            """,
        )
        delay_scenarios = rows_as_dict(
            conn,
            """
            SELECT delay_seconds,COUNT(*) AS samples,
                   COUNT(DISTINCT CAST(asset_id AS TEXT)||'|'||origin_day) AS asset_days
            FROM samples GROUP BY delay_seconds ORDER BY delay_seconds
            """,
        )
        event_lineage = row_as_dict(
            conn,
            """
            SELECT COUNT(*) AS links,COUNT(DISTINCT event_id) AS events,
                   COUNT(DISTINCT event_state_id) AS event_states,
                   COUNT(DISTINCT accession) AS accessions
            FROM sample_events
            """,
        )
        outcomes = rows_as_dict(
            conn,
            """
            SELECT horizon_sessions,status,COUNT(*) AS rows,
                   COUNT(DISTINCT sample_id) AS samples,MIN(origin_day) AS min_origin,
                   MAX(origin_day) AS max_origin
            FROM outcomes GROUP BY horizon_sessions,status ORDER BY horizon_sessions,status
            """,
        )
        groups = rows_as_dict(
            conn,
            """
            SELECT group_kind,COUNT(*) AS memberships,COUNT(DISTINCT group_id) AS groups,
                   COUNT(DISTINCT sample_id) AS samples
            FROM sample_groups GROUP BY group_kind ORDER BY group_kind
            """,
        )
        event_feature_keys: Counter[str] = Counter()
        market_feature_keys: Counter[str] = Counter()
        malformed_json_rows = 0
        for row in conn.execute("SELECT event_features_json,market_features_json FROM samples"):
            try:
                event_payload = json.loads(row["event_features_json"])
                market_payload = json.loads(row["market_features_json"])
                event_feature_keys.update(str(key) for key in event_payload)
                market_feature_keys.update(str(key) for key in market_payload)
            except (TypeError, json.JSONDecodeError):
                malformed_json_rows += 1

    return {
        "layer": "historical_event_dataset",
        "unit": "asset_close_delay_scenario",
        "summary": summary,
        "by_asset": by_asset,
        "delay_scenarios": delay_scenarios,
        "scenario_dependence_warning": "delay scenarios are sensitivity views, not independent events",
        "event_lineage": event_lineage,
        "outcomes_are_not_features": outcomes,
        "dependence_groups": groups,
        "event_feature_keys": sorted(event_feature_keys),
        "market_feature_keys": sorted(market_feature_keys),
        "malformed_feature_json_rows": malformed_json_rows,
        "historical_semantics": "corrected_close_aligned_historical_reconstruction_strict_pit_false",
        "feature_eligibility": "SPARSE_EVENT_INCREMENT_RESEARCH_ONLY",
    }


def profile_information_capture(
    path: Path,
    core: Mapping[str, Any],
    opened_paths: list[str],
) -> dict[str, Any]:
    with closing(connect_read_only(path, opened_paths)) as conn:
        present_tables = table_names(conn)
        row_counts = {
            table: int(scalar(conn, f"SELECT COUNT(*) FROM {qident(table)}") or 0)
            for table in (
                "source_observations",
                "expectation_observations",
                "scheduled_event_window_observations",
                "scheduled_event_observations",
                "economic_fact_observations",
                "news_document_observations",
                "news_asset_annotations",
                "news_story_cluster_candidates",
            )
            if table in present_tables
        }
        sources = rows_as_dict(
            conn,
            """
            SELECT source_type,source_name,strict_pit,COUNT(*) AS observations,
                   MIN(available_at) AS min_available,MAX(available_at) AS max_available,
                   MIN(retrieved_at) AS min_retrieved,MAX(retrieved_at) AS max_retrieved
            FROM source_observations
            GROUP BY source_type,source_name,strict_pit ORDER BY observations DESC
            """,
        )
        source_clock_violations = int(
            scalar(
                conn,
                """
                SELECT COUNT(*) FROM source_observations
                WHERE strict_pit=1 AND julianday(available_at)>julianday(retrieved_at)
                """,
            )
            or 0
        )
        expectation_summary = row_as_dict(
            conn,
            """
            SELECT COUNT(*) AS rows,COUNT(DISTINCT asset_ticker) AS assets,
                   COUNT(DISTINCT entity_key) AS entities,
                   COUNT(DISTINCT source_observation_id) AS source_snapshots,
                   COUNT(DISTINCT available_at) AS availability_instants,
                   MIN(available_at) AS min_available,MAX(available_at) AS max_available,
                   MIN(provider_as_of) AS min_provider_as_of,
                   MAX(provider_as_of) AS max_provider_as_of,
                   SUM(CASE WHEN provider_as_of IS NULL THEN 1 ELSE 0 END) AS missing_provider_as_of,
                   SUM(CASE WHEN strict_pit=1 THEN 1 ELSE 0 END) AS strict_pit_rows
            FROM expectation_observations
            """,
        )
        expectation_types = rows_as_dict(
            conn,
            """
            SELECT expectation_type,metric_key,statistic_key,COUNT(*) AS rows,
                   COUNT(DISTINCT asset_ticker) AS assets
            FROM expectation_observations
            GROUP BY expectation_type,metric_key,statistic_key ORDER BY rows DESC
            """,
        )
        expectation_tickers = {
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT asset_ticker FROM expectation_observations WHERE asset_ticker IS NOT NULL"
            )
        }
        schedule_summary = row_as_dict(
            conn,
            """
            SELECT COUNT(*) AS rows,COUNT(DISTINCT asset_ticker) AS assets,
                   COUNT(DISTINCT source_observation_id) AS source_snapshots,
                   MIN(available_at) AS min_available,MAX(available_at) AS max_available,
                   MIN(scheduled_date) AS min_scheduled,MAX(scheduled_date) AS max_scheduled,
                   SUM(CASE WHEN strict_pit=1 THEN 1 ELSE 0 END) AS strict_pit_rows,
                   SUM(CASE WHEN date(scheduled_date)<date(available_at) THEN 1 ELSE 0 END) AS already_past_when_captured
            FROM scheduled_event_window_observations
            """,
        )
        schedule_types = rows_as_dict(
            conn,
            """
            SELECT event_type,event_status,time_precision,COUNT(*) AS rows,
                   COUNT(DISTINCT asset_ticker) AS assets
            FROM scheduled_event_window_observations
            GROUP BY event_type,event_status,time_precision ORDER BY rows DESC
            """,
        )
        schedule_tickers = {
            str(row[0])
            for row in conn.execute(
                "SELECT DISTINCT asset_ticker FROM scheduled_event_window_observations WHERE asset_ticker IS NOT NULL"
            )
        }
        historical_overlap = row_as_dict(
            conn,
            """
            SELECT SUM(CASE WHEN available_at<=? THEN 1 ELSE 0 END) AS source_rows_available_by_core_end,
                   COUNT(*) AS source_rows_total
            FROM source_observations
            """,
            (str(core["max_state_time"]),),
        )

    core_tickers = set(core["tickers"])
    return {
        "layer": "prospective_information_capture",
        "unit": "source_snapshot_and_derived_observation",
        "row_counts": row_counts,
        "sources": sources,
        "strict_pit_source_clock_violations": source_clock_violations,
        "expectations": {
            "summary": expectation_summary,
            "metrics": expectation_types,
            "asset_tickers": sorted(expectation_tickers),
            "core_asset_tickers": sorted(expectation_tickers & core_tickers),
            "core_asset_overlap": len(expectation_tickers & core_tickers),
            "core_asset_fraction": len(expectation_tickers & core_tickers) / len(core_tickers) if core_tickers else None,
            "non_independence_note": "rows are metrics/periods within a small number of source snapshots, not independent vintages",
        },
        "scheduled_events": {
            "summary": schedule_summary,
            "types": schedule_types,
            "asset_tickers": sorted(schedule_tickers),
            "core_asset_tickers": sorted(schedule_tickers & core_tickers),
            "core_asset_overlap": len(schedule_tickers & core_tickers),
            "core_asset_fraction": len(schedule_tickers & core_tickers) / len(core_tickers) if core_tickers else None,
        },
        "historical_core_overlap": historical_overlap,
        "historical_semantics": "genuine strict-PIT capture beginning after frozen historical Core end",
        "feature_eligibility": "PROSPECTIVE_ACCUMULATION_ONLY_NOT_HISTORICAL_BACKFILL",
    }


def profile_graph(
    entity_path: Path,
    relation_path: Path,
    core: Mapping[str, Any],
    opened_paths: list[str],
) -> dict[str, Any]:
    with closing(connect_read_only(entity_path, opened_paths)) as conn:
        entities = row_as_dict(
            conn,
            """
            SELECT COUNT(*) AS evidence_buckets,
                   COUNT(DISTINCT registrant_asset_id) AS registrant_assets,
                   COUNT(DISTINCT normalized_legal_name) AS legal_names,
                   SUM(CASE WHEN identity_status='canonical' THEN 1 ELSE 0 END) AS canonical_buckets,
                   MIN(first_evidence_available_at) AS min_first_evidence,
                   MAX(last_evidence_available_at) AS max_last_evidence
            FROM identity_evidence_buckets
            """,
        )
        entity_statuses = rows_as_dict(
            conn,
            """
            SELECT identity_status,jurisdiction_status,COUNT(*) AS buckets,
                   COUNT(DISTINCT registrant_asset_id) AS registrant_assets,
                   SUM(evidence_occurrence_count) AS evidence_occurrences
            FROM identity_evidence_buckets
            GROUP BY identity_status,jurisdiction_status ORDER BY buckets DESC
            """,
        )
        entity_assets = {
            int(row[0])
            for row in conn.execute("SELECT DISTINCT registrant_asset_id FROM identity_evidence_buckets")
        }

    with closing(connect_read_only(relation_path, opened_paths)) as conn:
        claims = row_as_dict(
            conn,
            """
            SELECT COUNT(*) AS claims,COUNT(DISTINCT registrant_asset_id) AS registrant_assets,
                   COUNT(DISTINCT resolved_named_entity_id) AS resolved_named_entities,
                   SUM(CASE WHEN edge_ready=1 THEN 1 ELSE 0 END) AS edge_ready_claims,
                   SUM(CASE WHEN availability_is_point_in_time=1 THEN 1 ELSE 0 END) AS strict_pit_claims,
                   MIN(evidence_available_at) AS min_available,
                   MAX(evidence_available_at) AS max_available
            FROM evidence_claims
            """,
        )
        claim_types = rows_as_dict(
            conn,
            """
            SELECT claim_kind,resolution_status,edge_ready,availability_is_point_in_time AS strict_pit,
                   COUNT(*) AS claims,COUNT(DISTINCT registrant_asset_id) AS registrant_assets
            FROM evidence_claims
            GROUP BY claim_kind,resolution_status,edge_ready,availability_is_point_in_time
            ORDER BY claims DESC
            """,
        )
        relation_assets = {
            int(row[0])
            for row in conn.execute(
                "SELECT DISTINCT registrant_asset_id FROM evidence_claims"
            )
        }
    core_assets = set(core["asset_ids"])
    registrant_assets = entity_assets | relation_assets
    asset_id_to_ticker = core["asset_id_to_ticker"]
    registrant_catalog = [
        {
            "asset_id": asset_id,
            "ticker": asset_id_to_ticker.get(asset_id),
            "in_core": asset_id in core_assets,
        }
        for asset_id in sorted(registrant_assets)
    ]
    return {
        "layer": "graph_evidence",
        "entities": {"summary": entities, "statuses": entity_statuses},
        "relations": {"summary": claims, "types": claim_types},
        "registrant_asset_catalog": registrant_catalog,
        "core_registrant_asset_overlap": len(registrant_assets & core_assets),
        "core_registrant_asset_fraction": len(registrant_assets & core_assets) / len(core_assets) if core_assets else None,
        "historical_semantics": "historical reconstructed evidence; strict PIT false",
        "feature_eligibility": "EVIDENCE_ONLY_NO_CANONICAL_ENTITIES_NO_EDGE_READY_GRAPH",
    }


def build_inventory(
    generated_at: str,
    core_profile: Mapping[str, Any],
    market_profile: Mapping[str, Any],
    external_profile: Mapping[str, Any],
    financial_profile: Mapping[str, Any],
    event_profile: Mapping[str, Any],
    information_profile: Mapping[str, Any],
    graph_profile: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": generated_at,
        "interpretation_rule": "presence, causal availability, joinability, historical depth and model eligibility are separate properties",
        "layers": {
            "market_core": core_profile,
            "market_source": market_profile,
            "external_market": external_profile,
            "financial_conditions": financial_profile,
            "historical_events": event_profile,
            "prospective_information": information_profile,
            "graph_evidence": graph_profile,
        },
        "training_authorized": False,
    }


def build_coverage_matrix(
    generated_at: str,
    core: Mapping[str, Any],
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    layers = inventory["layers"]
    market = layers["market_source"]
    external = layers["external_market"]
    financial = layers["financial_conditions"]
    events = layers["historical_events"]
    information = layers["prospective_information"]
    graph = layers["graph_evidence"]
    core_states = int(core["states"])
    core_assets = int(core["assets"])

    rows = [
        {
            "layer": "daily_ohlcv_source",
            "data_present": bool(market["daily_ohlcv"]["summary"].get("versions")),
            "unit": "asset_day_version",
            "date_min": market["daily_ohlcv"]["summary"].get("min_day"),
            "date_max": market["daily_ohlcv"]["summary"].get("max_day"),
            "assets": market["daily_ohlcv"]["summary"].get("assets"),
            "core_state_join_fraction": 1.0,
            "strict_pit": False,
            "eligibility": "SOURCE_FOR_HISTORICAL_CORE_RECONSTRUCTION",
        },
        {
            "layer": "core_own_cross_sector_state",
            "data_present": core_states > 0,
            "unit": "asset_origin_day",
            "date_min": layers["market_core"]["summary"].get("min_day"),
            "date_max": layers["market_core"]["summary"].get("max_day"),
            "assets": core_assets,
            "core_state_join_fraction": 1.0,
            "strict_pit": False,
            "eligibility": "HISTORICAL_RESEARCH_CONTEXT",
        },
        {
            "layer": "spy_qqq_iwm_context",
            "data_present": bool(external["summary"].get("rows")),
            "unit": "origin_day",
            "date_min": external["summary"].get("min_day"),
            "date_max": external["summary"].get("max_day"),
            "assets": "market_level",
            "core_state_join_fraction": external["core_join_coverage"].get("matched_state_fraction"),
            "strict_pit": False,
            "eligibility": external["feature_eligibility"],
        },
        {
            "layer": "vix_rates_credit_context",
            "data_present": bool(financial["summary"].get("rows")),
            "unit": "origin_day",
            "date_min": financial["summary"].get("min_day"),
            "date_max": financial["summary"].get("max_day"),
            "assets": "market_level",
            "core_state_join_fraction": financial["core_join_coverage"].get("matched_state_fraction"),
            "strict_pit": False,
            "eligibility": financial["feature_eligibility"],
        },
        {
            "layer": "intraday_1m_legacy",
            "data_present": bool(market["intraday_legacy"]["bars"]),
            "unit": "asset_minute",
            "date_min": market["intraday_legacy"]["bars"][0].get("min_time") if market["intraday_legacy"]["bars"] else None,
            "date_max": market["intraday_legacy"]["bars"][0].get("max_time") if market["intraday_legacy"]["bars"] else None,
            "assets": market["intraday_legacy"]["bars"][0].get("assets") if market["intraday_legacy"]["bars"] else 0,
            "core_state_join_fraction": None,
            "strict_pit": "not_explicitly_encoded",
            "eligibility": market["intraday_legacy"]["feature_eligibility"],
        },
        {
            "layer": "historical_sec_event_state",
            "data_present": bool(events["summary"].get("samples")),
            "unit": events["unit"],
            "date_min": events["summary"].get("min_day"),
            "date_max": events["summary"].get("max_day"),
            "assets": events["summary"].get("assets"),
            "core_asset_fraction": events["summary"].get("assets", 0) / core_assets if core_assets else None,
            "strict_pit": False,
            "eligibility": events["feature_eligibility"],
        },
        {
            "layer": "legacy_news_metadata",
            "data_present": bool(market["legacy_news"]["summary"].get("documents")),
            "unit": "document_asset_link",
            "date_min": market["legacy_news"]["summary"].get("min_published"),
            "date_max": market["legacy_news"]["summary"].get("max_published"),
            "assets": market["legacy_news"]["asset_links"].get("linked_assets"),
            "core_state_join_fraction": None,
            "strict_pit": False,
            "eligibility": market["legacy_news"]["feature_eligibility"],
        },
        {
            "layer": "prospective_analyst_expectations",
            "data_present": bool(information["expectations"]["summary"].get("rows")),
            "unit": "metric_period_statistic_within_source_snapshot",
            "date_min": information["expectations"]["summary"].get("min_available"),
            "date_max": information["expectations"]["summary"].get("max_available"),
            "assets": information["expectations"]["summary"].get("assets"),
            "core_asset_fraction": information["expectations"].get("core_asset_fraction"),
            "strict_pit": True,
            "eligibility": information["feature_eligibility"],
        },
        {
            "layer": "prospective_scheduled_earnings",
            "data_present": bool(information["scheduled_events"]["summary"].get("rows")),
            "unit": "asset_scheduled_event_snapshot",
            "date_min": information["scheduled_events"]["summary"].get("min_available"),
            "date_max": information["scheduled_events"]["summary"].get("max_available"),
            "assets": information["scheduled_events"]["summary"].get("assets"),
            "core_asset_fraction": information["scheduled_events"].get("core_asset_fraction"),
            "strict_pit": True,
            "eligibility": information["feature_eligibility"],
        },
        {
            "layer": "graph_structural_evidence",
            "data_present": bool(graph["relations"]["summary"].get("claims")),
            "unit": "evidence_claim",
            "date_min": graph["relations"]["summary"].get("min_available"),
            "date_max": graph["relations"]["summary"].get("max_available"),
            "assets": graph["relations"]["summary"].get("registrant_assets"),
            "core_asset_fraction": graph.get("core_registrant_asset_fraction"),
            "strict_pit": False,
            "eligibility": graph["feature_eligibility"],
        },
        {
            "layer": "macro_release_vintages",
            "data_present": False,
            "unit": "release_vintage",
            "date_min": None,
            "date_max": None,
            "assets": "market_level",
            "core_state_join_fraction": 0.0,
            "strict_pit": False,
            "eligibility": "ABSENT",
        },
        {
            "layer": "option_implied_state",
            "data_present": False,
            "unit": "asset_expiry_surface_snapshot",
            "date_min": None,
            "date_max": None,
            "assets": 0,
            "strict_pit": False,
            "eligibility": "ABSENT",
        },
        {
            "layer": "survivorship_free_universe_history",
            "data_present": False,
            "unit": "asset_universe_membership_interval",
            "date_min": None,
            "date_max": None,
            "assets": 0,
            "strict_pit": False,
            "eligibility": "ABSENT_CURRENT_COHORT_ONLY",
        },
    ]
    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": generated_at,
        "reference_domain": {
            "states": core_states,
            "assets": core_assets,
            "origin_days": int(core["days"]),
            "max_day": core["max_day"],
        },
        "coverage_rows": rows,
        "training_authorized": False,
    }


def build_feature_readiness(
    generated_at: str,
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": generated_at,
        "ready_historical_research_blocks": [
            {
                "block": "own_state",
                "use": "strong existing baseline/state scale and shape tests",
                "constraint": "historical reconstruction, current-company cohort",
            },
            {
                "block": "cross_section_state",
                "use": "separate incremental distributional test",
                "constraint": "failed in earlier pooled scalar specification; not automatically retained",
            },
            {
                "block": "sector_state",
                "use": "separate incremental distributional test",
                "constraint": "context is replicated by day/sector and requires dependence-aware controls",
            },
            {
                "block": "broad_market_state",
                "use": "SPY/QQQ/IWM context with exact Core-day coverage",
                "constraint": "historical PIT=false; prior scalar increment was not promoted",
            },
            {
                "block": "financial_conditions_state",
                "use": "lagged VIX plus rates/credit context with exact Core-day coverage",
                "constraint": "historical PIT=false; prior scalar increment was not promoted",
            },
        ],
        "research_only_sparse_blocks": [
            {
                "block": "historical_sec_event_state",
                "reason": "10 assets, historical reconstruction, sparse event origins, delay scenarios dependent",
            }
        ],
        "prospective_accumulation_only": [
            {
                "block": "analyst_expectations",
                "reason": "strict-PIT but only a first capture vintage and a small asset subset",
            },
            {
                "block": "scheduled_earnings",
                "reason": "strict-PIT first snapshot; only part of the Core universe overlaps",
            },
            {
                "block": "future_news_capture",
                "reason": "schema exists but no strict-PIT documents captured yet",
            },
        ],
        "not_model_ready": [
            {
                "block": "legacy_news",
                "reason": "bulk-ingested historical metadata lacks available_at and raw text; no populated feature table",
            },
            {
                "block": "legacy_intraday",
                "reason": "roughly one trading week and no complete modern causal state contract",
            },
            {
                "block": "graph",
                "reason": "identity buckets are non-canonical, zero edge-ready claims and no strict-PIT relation claims",
            },
            {
                "block": "legacy_macro",
                "reason": "only isolated same-day observations with no release/vintage semantics",
            },
        ],
        "absent_high_value_blocks": [
            "macro_release_vintages",
            "fundamental_numeric_state_with_filing_vintages",
            "actual_vs_expectation_surprise_history",
            "analyst_revision_time_series",
            "option_implied_volatility_skew_and_term_structure",
            "positioning_flows_short_interest_and_liquidity_cost_state",
            "survivorship_free_historical_universe_membership",
            "canonical_time-aware_structural_graph",
        ],
        "training_authorized": False,
        "materialization_authorized": False,
        "note": "readiness means data/clock integration can be planned; it is not a predictive promotion",
    }


def build_gap_plan(
    generated_at: str,
    inventory: Mapping[str, Any],
) -> dict[str, Any]:
    information = inventory["layers"]["prospective_information"]
    news = inventory["layers"]["market_source"]["legacy_news"]
    graph = inventory["layers"]["graph_evidence"]
    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": generated_at,
        "principle": "acquire information by falsifiable blocks; do not hide missing information with more model capacity",
        "priorities": [
            {
                "priority": 1,
                "work": "materialize a shared read-only context registry from existing Core/day blocks",
                "why": "the dense market, cross-section, sector, ETF and financial-condition clocks already align exactly",
                "next_gate": "separate incremental Context V003 A/B/C comparisons against vol63+tau",
            },
            {
                "priority": 2,
                "work": "continue append-only expectation and scheduled-event capture",
                "current_evidence": {
                    "expectation_core_assets": information["expectations"]["core_asset_overlap"],
                    "expectation_source_snapshots": information["expectations"]["summary"].get("source_snapshots"),
                    "scheduled_core_assets": information["scheduled_events"]["core_asset_overlap"],
                },
                "missing": "repeated vintages, revisions and reported actuals needed for surprise",
                "next_gate": "coverage/vintage audit before any predictive use",
            },
            {
                "priority": 3,
                "work": "start a strict-PIT append-only news/evidence capture",
                "current_evidence": {
                    "legacy_documents": news["summary"].get("documents"),
                    "legacy_media_documents": news["media_or_other"].get("documents"),
                    "strict_pit_news_documents": information["row_counts"].get("news_document_observations", 0),
                },
                "sources_to_stage": [
                    "company Investor Relations and official releases",
                    "SEC live submissions/filings",
                    "one licensed or reproducible wire/news provider",
                    "selected macro authorities",
                ],
                "required_contract": "raw bytes/hash, published_at, first_seen_at, retrieved_at, available_at, source identity, asset links and causal deduplication",
                "next_gate": "source/clock/duplicate audit; no sentiment/reliability hardcoding",
            },
            {
                "priority": 4,
                "work": "derive structured fundamentals and actual-vs-expectation surprise",
                "why": "current SEC taxonomy knows that an event occurred but not the economically important numeric change versus expectations",
                "required_contract": "filing/version availability, units, fiscal periods, revisions and no hindsight-restated feature values",
                "next_gate": "direct event increment before graph propagation",
            },
            {
                "priority": 5,
                "work": "add one orthogonal market information block at a time",
                "candidates": [
                    "option-implied volatility/skew/term structure",
                    "macro releases with vintage and release timestamps",
                    "positioning/flows/short interest",
                    "liquidity/spread and realistic cost state",
                ],
                "next_gate": "capacity-matched incremental OOS score versus the strongest retained context",
            },
            {
                "priority": 6,
                "work": "finish identity hygiene before calling the relation evidence a graph",
                "current_evidence": {
                    "canonical_identity_buckets": graph["entities"]["summary"].get("canonical_buckets"),
                    "edge_ready_claims": graph["relations"]["summary"].get("edge_ready_claims"),
                    "registrant_assets": graph["relations"]["summary"].get("registrant_assets"),
                },
                "next_gate": "direct event information must add OOS value before propagation",
            },
            {
                "priority": 7,
                "work": "build a survivorship-aware universe/history contract",
                "why": "497 assets are a current-company historical cohort, not historical market membership",
                "next_gate": "claims may expand beyond the current cohort only after membership/delisting/ticker lineage exists",
            },
        ],
        "explicitly_not_next": [
            "larger black-box model on the same information",
            "treating 5,411 expectation rows as 5,411 independent vintages",
            "using legacy news published_at as historical available_at",
            "promoting unresolved relation mentions to graph edges",
            "opening temporal holdouts to select an information block",
            "modifying or reading the V009 registry from this track",
        ],
        "training_authorized": False,
    }


def build_integration_plan(
    generated_at: str,
    config: Mapping[str, Any],
) -> dict[str, Any]:
    return {
        "contract_version": CONTRACT_VERSION,
        "generated_at": generated_at,
        "status": "PLAN_ONLY_NO_DATASET_MATERIALIZATION_NO_TRAINING",
        "future_artifact": "data/processed/market_information_state_v001.db",
        "design": {
            "principle": "keep native statistical units and join lazily; do not replicate every day-level field over every asset/tau row",
            "tables": [
                {
                    "name": "information_state_origins",
                    "key": "state_id",
                    "content": "exact Core asset/day/state_time identity only",
                },
                {
                    "name": "core_feature_block_manifest",
                    "key": "feature_block_version",
                    "content": "source hash/schema, approved feature list and historical PIT semantics",
                },
                {
                    "name": "day_context_external_market",
                    "key": "trading_day",
                    "content": "SPY/QQQ/IWM block kept once per day",
                },
                {
                    "name": "day_context_financial_conditions",
                    "key": "trading_day",
                    "content": "lagged VIX/rates/credit block kept once per day",
                },
                {
                    "name": "sparse_event_state_links",
                    "key": "event_state_id, state_id, scenario",
                    "content": "separate sparse bridge; never forward-filled as a dense market fact",
                },
                {
                    "name": "prospective_information_links",
                    "key": "source_observation_id, first_eligible_state_id",
                    "content": "future append-only strict-PIT bridge; empty historical backfill is expected",
                },
                {
                    "name": "integration_gates",
                    "key": "gate_name",
                    "content": "coverage, causal clock, source stability and feature eligibility evidence",
                },
            ],
        },
        "experiment_ladder": config["planned_increment_ladder"],
        "evaluation_contract": {
            "reference": "vol63_plus_tau",
            "increments_tested_separately": True,
            "capacity_controls": "same-capacity deranged added block",
            "development_taus": "existing development anchors only",
            "sealed_taus": [7, 17, 42, 90, 180],
            "sealed_tau_policy": "at most one preregistered contextual candidate may open them once",
            "time_split": "purged expanding walk-forward by target_day",
            "dependence": "whole-origin-day cross-tau panels",
            "no_rescue": "no stacking, feature deletion or horizon selection after observing a failed rung",
        },
        "event_bridge_after_context": {
            "comparison": "market_only_distribution versus market_plus_direct_event_information",
            "capacity_control_required": True,
            "event_groups_preserved": True,
            "graph_blocked_until_direct_event_increment": True,
        },
        "guards": {
            "training_authorized": False,
            "materialization_authorized_by_this_plan": False,
            "source_databases_read_only": True,
            "V009_loaded_or_modified": False,
            "outcomes_as_features": False,
        },
    }


def build_markdown_summary(
    audit: Mapping[str, Any],
    inventory: Mapping[str, Any],
    coverage: Mapping[str, Any],
    gaps: Mapping[str, Any],
) -> str:
    layers = inventory["layers"]
    core = layers["market_core"]["summary"]
    daily = layers["market_source"]["daily_ohlcv"]["summary"]
    intraday = layers["market_source"]["intraday_legacy"]["bars"]
    news = layers["market_source"]["legacy_news"]
    events = layers["historical_events"]["summary"]
    info = layers["prospective_information"]
    graph = layers["graph_evidence"]
    intraday_first = intraday[0] if intraday else {}
    event_tickers = ", ".join(row["ticker"] for row in layers["historical_events"]["by_asset"])
    expectation_tickers = ", ".join(info["expectations"]["asset_tickers"])
    graph_tickers = ", ".join(
        row["ticker"] or f"asset_id={row['asset_id']}"
        for row in graph["registrant_asset_catalog"]
    )

    lines = [
        "# Information Integration Readiness V001 — inventario local",
        "",
        f"Generado: {audit['generated_at']}",
        "",
        f"Estado: `{audit['status']}`. Este estado no autoriza materialización ni entrenamiento.",
        "",
        "## Resumen de lo que existe",
        "",
        "| Capa | Cobertura observada | Interpretación |",
        "|---|---|---|",
        f"| Core diario | {core.get('states'):,} estados, {core.get('assets')} activos, {core.get('min_day')} a {core.get('max_day')} | OHLCV/estado histórico PIT=0; base de investigación |",
        f"| Fuente OHLCV diaria | {daily.get('asset_days'):,} asset-days, {daily.get('assets')} activos, {daily.get('min_day')} a {daily.get('max_day')} | Yahoo; adjusted close sólo auditoría |",
        f"| Velas intradía | {intraday_first.get('bars', 0):,} barras {intraday_first.get('interval', '')}, {intraday_first.get('assets', 0)} activos, {intraday_first.get('min_time')} a {intraday_first.get('max_time')} | piloto ancho pero temporalmente muy corto |",
        f"| Noticias legacy | {news['summary'].get('documents', 0):,} documentos; {news['media_or_other'].get('documents', 0):,} no-SEC | carga histórica sin available_at ni raw text; no lista para modelo |",
        f"| Eventos SEC corregidos | {events.get('samples', 0):,} escenarios, {events.get('assets')} activos, {events.get('min_day')} a {events.get('max_day')} | reconstrucción histórica PIT=0 y escenarios dependientes |",
        f"| Expectativas prospectivas | {info['expectations']['summary'].get('rows', 0):,} campos, {info['expectations']['summary'].get('assets')} activos, {info['expectations']['summary'].get('source_snapshots')} snapshots | strict-PIT real, pero recién iniciado |",
        f"| Calendario prospectivo | {info['scheduled_events']['summary'].get('rows', 0):,} filas; solapamiento Core {info['scheduled_events'].get('core_asset_overlap')} activos | strict-PIT real, primera captura |",
        f"| Grafo | {graph['relations']['summary'].get('claims', 0):,} claims, {graph['relations']['summary'].get('edge_ready_claims')} edge-ready | evidencia, no grafo canónico/model-visible |",
        "",
        "## Qué puede integrarse ahora",
        "",
        "El Core, cross-section, sector, SPY/QQQ/IWM y condiciones financieras tienen reloj diario compatible. Deben probarse como bloques incrementales separados; su mera cobertura no implica valor predictivo.",
        "",
        "Los eventos SEC pueden enlazarse en una pista histórica dispersa de diez activos. Las expectativas y futuros documentos de noticias sólo pueden acumularse prospectivamente porque no existían antes del cierre del Core histórico.",
        "",
        "## Empresas y fuentes identificables",
        "",
        f"- Eventos SEC corregidos: {event_tickers}.",
        f"- Expectativas prospectivas: {expectation_tickers}.",
        f"- Evidencia relacional: {graph_tickers}.",
        f"- El catálogo completo de {core.get('assets')} activos del Core está en `inventory_report.json` → `layers.market_core.asset_coverage`.",
        "- El catálogo de activos de precios está en `layers.market_source.assets.catalog`; la cobertura de noticias por ticker está en `layers.market_source.legacy_news.document_coverage_by_asset`; las 132 fuentes están en `layers.market_source.legacy_news.all_sources`.",
        "",
        "Advertencias puntuales: las expectativas actuales carecen de `provider_as_of`; el calendario incluye eventos que ya estaban en el pasado al capturarse; ambos son prospectivos y todavía no forman historia suficiente.",
        "",
        "## Qué falta primero",
        "",
    ]
    spanish_priorities = [
        "materializar un registro compartido de contexto usando los bloques diarios ya existentes",
        "continuar la captura append-only de expectativas y eventos programados",
        "iniciar una captura de noticias/evidencia strict-PIT y append-only",
        "derivar fundamentales estructurados y sorpresa real versus expectativa",
        "incorporar un bloque ortogonal de información de mercado por vez",
    ]
    for priority, work in enumerate(spanish_priorities, start=1):
        lines.append(f"{priority}. {work}.")
    lines.extend(
        [
            "",
            "## Gate",
            "",
            "- Fuentes abiertas sólo en lectura.",
            "- Outcomes no elegibles como features.",
            "- V009 no abierto ni modificado.",
            "- Entrenamiento bloqueado.",
            "",
            f"Detalle estructurado: `{REPORT_FILENAMES['inventory']}`, `{REPORT_FILENAMES['coverage']}`, `{REPORT_FILENAMES['readiness']}`, `{REPORT_FILENAMES['gaps']}` y `{REPORT_FILENAMES['plan']}`.",
            "",
        ]
    )
    return "\n".join(lines)


def build_reports(
    repo_root: Path,
    config_path: Path,
    output_dir: Path,
    *,
    write_outputs: bool = True,
) -> dict[str, Any]:
    repo_root = repo_root.resolve()
    config_path = config_path.resolve()
    output_dir = output_dir.resolve()
    config = json.loads(config_path.read_text(encoding="utf-8"))
    config_validation = validate_config(config)
    generated_at = utc_now()

    source_paths: dict[str, Path] = {
        "reference_state": resolve_path(repo_root, str(config["reference_state"]["database"]))
    }
    for logical_name, source in config.get("sources", {}).items():
        source_paths[logical_name] = resolve_path(repo_root, str(source["database"]))

    before_states = {name: file_state(path) for name, path in source_paths.items()}
    opened_paths: list[str] = []
    schemas = {
        name: inspect_schema(path, name, opened_paths)
        for name, path in source_paths.items()
        if name in REQUIRED_TABLES
    }
    missing_or_invalid_sources = [
        name
        for name, report in schemas.items()
        if not report.get("exists") or report.get("missing_required_tables")
    ]

    profiles: dict[str, Any] = {}
    internal: dict[str, Any] = {}
    runtime_errors: list[str] = []
    if config_validation["valid"] and not missing_or_invalid_sources:
        try:
            core_profile, internal = profile_core(
                source_paths["reference_state"], config, opened_paths
            )
            profiles["core"] = core_profile
            profiles["market"] = profile_market_source(
                source_paths["market_source"], internal, opened_paths
            )
            profiles["external"] = profile_day_context(
                source_paths["external_market"],
                "external_market_state",
                config["sources"]["external_market"],
                "broad_market_state",
                config,
                internal,
                opened_paths,
            )
            profiles["financial"] = profile_day_context(
                source_paths["financial_conditions"],
                "financial_conditions_state",
                config["sources"]["financial_conditions"],
                "financial_conditions_state",
                config,
                internal,
                opened_paths,
            )
            profiles["event"] = profile_event_dataset(
                source_paths["historical_event_dataset"], opened_paths
            )
            profiles["information"] = profile_information_capture(
                source_paths["prospective_information"], internal, opened_paths
            )
            profiles["graph"] = profile_graph(
                source_paths["graph_entity_evidence"],
                source_paths["graph_relation_evidence"],
                internal,
                opened_paths,
            )
        except Exception as exc:
            runtime_errors.append(f"{type(exc).__name__}: {exc}")

    after_states = {name: file_state(path) for name, path in source_paths.items()}
    stable_sources = before_states == after_states
    forbidden_tokens = [
        str(value).lower()
        for value in config.get("guards", {}).get("forbidden_source_path_tokens", [])
    ]
    forbidden_open_hits = [
        path
        for path in opened_paths
        if any(token and token in path.lower() for token in forbidden_tokens)
    ]

    hard_gates: dict[str, bool] = {
        "config_valid": bool(config_validation["valid"]),
        "all_required_sources_and_tables_present": not missing_or_invalid_sources,
        "source_files_stable_during_audit": stable_sources,
        "all_database_connections_read_only": True,
        "no_forbidden_database_opened": not forbidden_open_hits,
        "no_declared_outcome_or_future_feature": not config_validation["feature_leakage_hits"],
        "runtime_completed": not runtime_errors,
    }

    if profiles:
        core_profile = profiles["core"]
        hard_gates.update(
            {
                "core_state_identity_unique": core_profile["duplicate_state_ids"] == 0
                and core_profile["duplicate_asset_days"] == 0,
                "core_feature_version_matches": bool(core_profile["feature_version_matches"]),
                "declared_feature_columns_exist": not any(
                    block["missing_columns"]
                    for block in core_profile["feature_blocks"].values()
                )
                and not profiles["external"]["missing_feature_columns"]
                and not profiles["financial"]["missing_feature_columns"],
                "external_context_exact_core_coverage": profiles["external"]["core_join_coverage"]["matched_core_states"]
                == internal["states"]
                and profiles["external"]["duplicate_join_days"] == 0
                and all(
                    int(value) == 0
                    for value in profiles["external"]["core_join_feature_null_counts"].values()
                ),
                "financial_context_exact_core_coverage": profiles["financial"]["core_join_coverage"]["matched_core_states"]
                == internal["states"]
                and profiles["financial"]["duplicate_join_days"] == 0
                and all(
                    int(value) == 0
                    for value in profiles["financial"]["core_join_feature_null_counts"].values()
                ),
                "strict_pit_capture_source_clocks_valid": profiles["information"]["strict_pit_source_clock_violations"]
                == 0,
                "prospective_capture_not_relabelled_historical": int(
                    profiles["information"]["historical_core_overlap"].get(
                        "source_rows_available_by_core_end", 0
                    )
                    or 0
                )
                == 0,
                "graph_current_classification_preserved": profiles["graph"][
                    "feature_eligibility"
                ]
                == "EVIDENCE_ONLY_NO_CANONICAL_ENTITIES_NO_EDGE_READY_GRAPH",
            }
        )

    passed = all(hard_gates.values())
    status = (
        "PASS_READ_ONLY_INFORMATION_INVENTORY_CONTEXT_PLAN_READY"
        if passed
        else "REVIEW_REQUIRED_INFORMATION_INTEGRATION_READINESS"
    )
    audit: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "generated_at": generated_at,
        "status": status,
        "read_only": True,
        "training_authorized": False,
        "materialization_authorized": False,
        "scientific_promotion": "NOT_EVALUATED",
        "config_path": str(config_path),
        "config_sha256": sha256_file(config_path),
        "config_validation": config_validation,
        "schemas": schemas,
        "source_file_states_before": before_states,
        "source_file_states_after": after_states,
        "opened_database_paths": sorted(set(opened_paths)),
        "forbidden_open_hits": forbidden_open_hits,
        "v009_isolation": "PASS_NOT_CONFIGURED_NOT_OPENED_NOT_MODIFIED",
        "hard_gates": hard_gates,
        "runtime_errors": runtime_errors,
        "interpretation": "a PASS authorizes only the next plan/materializer design review; it does not authorize training or feature promotion",
    }

    if profiles:
        inventory = build_inventory(
            generated_at,
            profiles["core"],
            profiles["market"],
            profiles["external"],
            profiles["financial"],
            profiles["event"],
            profiles["information"],
            profiles["graph"],
        )
        coverage = build_coverage_matrix(generated_at, internal, inventory)
        readiness = build_feature_readiness(generated_at, inventory)
        gaps = build_gap_plan(generated_at, inventory)
        plan = build_integration_plan(generated_at, config)
    else:
        partial = {
            "contract_version": CONTRACT_VERSION,
            "generated_at": generated_at,
            "status": "NOT_BUILT_DUE_TO_AUDIT_ERRORS",
            "training_authorized": False,
        }
        inventory = dict(partial)
        coverage = dict(partial)
        readiness = dict(partial)
        gaps = dict(partial)
        plan = dict(partial)

    reports: dict[str, Any] = {
        "audit": audit,
        "inventory": inventory,
        "coverage": coverage,
        "readiness": readiness,
        "gaps": gaps,
        "plan": plan,
    }

    if write_outputs:
        output_hashes: dict[str, Any] = {}
        for key in ("inventory", "coverage", "readiness", "gaps", "plan"):
            path = output_dir / REPORT_FILENAMES[key]
            payload = json_bytes(reports[key])
            atomic_write(path, payload)
            output_hashes[key] = {
                "path": str(path),
                "sha256": sha256_bytes(payload),
                "size_bytes": len(payload),
            }
        if profiles:
            summary = build_markdown_summary(audit, inventory, coverage, gaps)
        else:
            summary = "\n".join(
                [
                    "# Information Integration Readiness V001",
                    "",
                    f"Estado: `{audit['status']}`.",
                    "",
                    "El inventario detallado no se construyó porque faltan fuentes, tablas o un gate de configuración.",
                    "Revisar `audit.json`. Entrenamiento y materialización permanecen bloqueados.",
                    "",
                ]
            )
        summary_path = output_dir / REPORT_FILENAMES["summary"]
        summary_payload = (summary.rstrip() + "\n").encode("utf-8")
        atomic_write(summary_path, summary_payload)
        output_hashes["summary"] = {
            "path": str(summary_path),
            "sha256": sha256_bytes(summary_payload),
            "size_bytes": len(summary_payload),
        }
        audit["outputs"] = output_hashes
        audit_path = output_dir / REPORT_FILENAMES["audit"]
        audit_payload = json_bytes(audit)
        atomic_write(audit_path, audit_payload)
        reports["summary"] = summary
        reports["output_dir"] = str(output_dir)

    return reports


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only causal inventory and integration-readiness gate for Quant Market AI information layers."
    )
    parser.add_argument("--repo-root", default=str(ROOT))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output-dir", default=str(DEFAULT_OUTPUT_DIR))
    args = parser.parse_args()

    repo_root = Path(args.repo_root).resolve()
    config_path = Path(args.config)
    if not config_path.is_absolute():
        config_path = (repo_root / config_path).resolve()
    output_dir = Path(args.output_dir)
    if not output_dir.is_absolute():
        output_dir = (repo_root / output_dir).resolve()

    reports = build_reports(repo_root, config_path, output_dir, write_outputs=True)
    audit = reports["audit"]
    compact = {
        "status": audit["status"],
        "read_only": audit["read_only"],
        "training_authorized": audit["training_authorized"],
        "materialization_authorized": audit["materialization_authorized"],
        "v009_isolation": audit["v009_isolation"],
        "hard_gates": audit["hard_gates"],
        "output_dir": reports.get("output_dir"),
        "reports": REPORT_FILENAMES,
    }
    print(json.dumps(compact, indent=2, ensure_ascii=False, sort_keys=True))
    raise SystemExit(0 if audit["status"].startswith("PASS_") else 2)


if __name__ == "__main__":
    main()

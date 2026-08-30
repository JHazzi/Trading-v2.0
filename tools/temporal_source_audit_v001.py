from __future__ import annotations

import argparse
import json
import sqlite3
from collections import Counter
from pathlib import Path
from typing import Any

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "database" / "market_data_v2.db"
DEFAULT_CORE = ROOT / "data" / "processed" / "market_daily_v003_core.db"
DEFAULT_CONFIG = ROOT / "config" / "temporal_dataset_v001.json"
DEFAULT_OUTPUT = ROOT / "reports" / "temporal_source_audit_v001.json"

KEY_SOURCE_TABLES = (
    "assets",
    "price_bars",
    "price_bar_versions",
    "price_bar_observations",
    "price_data_quality",
    "daily_price_asof_configs",
    "market_sessions",
    "corporate_action_versions",
    "corporate_action_observations",
    "market_state_v002_snapshots",
    "realized_outcomes",
)
REQUIRED_SOURCE_TABLES = (
    "assets",
    "price_bars",
    "market_sessions",
    "corporate_action_versions",
    "corporate_action_observations",
)

EXPECTED_CORE_METADATA = {
    "source_asof_contract": "daily_price_asof_v1",
    "source_asof_mode": "historical_session_close_assumption",
    "state_clock": "exchange_session_close",
    "strict_historical_pit": False,
    "feature_version": "market_daily_state_v003_core",
    "label_version": "market_daily_reaction_v003_core",
    "target": "raw_close_t_to_raw_close_t_plus_h",
}


def qident(value: str) -> str:
    return '"' + value.replace('"', '""') + '"'


def connect_read_only(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    conn = sqlite3.connect(f"file:{resolved.as_posix()}?mode=ro", uri=True)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only = ON")
    return conn


def table_names(conn: sqlite3.Connection) -> list[str]:
    return [
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table' ORDER BY name"
        )
    ]


def table_columns(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    return [
        {
            "cid": int(r["cid"]),
            "name": str(r["name"]),
            "type": str(r["type"] or ""),
            "notnull": bool(r["notnull"]),
            "default": r["dflt_value"],
            "pk_position": int(r["pk"]),
        }
        for r in conn.execute(f"PRAGMA table_info({qident(table)})")
    ]


def table_indexes(conn: sqlite3.Connection, table: str) -> list[dict[str, Any]]:
    output: list[dict[str, Any]] = []
    for row in conn.execute(f"PRAGMA index_list({qident(table)})"):
        name = str(row["name"])
        columns = [
            str(r["name"])
            for r in conn.execute(f"PRAGMA index_info({qident(name)})")
        ]
        output.append(
            {
                "name": name,
                "unique": bool(row["unique"]),
                "origin": str(row["origin"]),
                "partial": bool(row["partial"]),
                "columns": columns,
            }
        )
    return output


def _json_safe(value: Any, max_text: int = 240) -> Any:
    if value is None or isinstance(value, (bool, int, float)):
        return value
    if isinstance(value, bytes):
        return f"<bytes:{len(value)}>"
    text = str(value)
    if len(text) > max_text:
        return text[:max_text] + f"...<truncated:{len(text)}>"
    return text


def sample_rows(
    conn: sqlite3.Connection,
    table: str,
    limit: int = 2,
    order_by_integer_pk_desc: bool = True,
) -> list[dict[str, Any]]:
    columns = table_columns(conn, table)
    integer_pk = next(
        (
            c["name"]
            for c in columns
            if c["pk_position"] == 1 and "INT" in c["type"].upper()
        ),
        None,
    )
    sql = f"SELECT * FROM {qident(table)}"
    if order_by_integer_pk_desc and integer_pk:
        sql += f" ORDER BY {qident(integer_pk)} DESC"
    sql += " LIMIT ?"
    return [
        {k: _json_safe(row[k]) for k in row.keys()}
        for row in conn.execute(sql, (int(limit),))
    ]


def max_integer_pk(conn: sqlite3.Connection, table: str) -> dict[str, Any] | None:
    columns = table_columns(conn, table)
    integer_pk = next(
        (
            c["name"]
            for c in columns
            if c["pk_position"] == 1 and "INT" in c["type"].upper()
        ),
        None,
    )
    if not integer_pk:
        return None
    value = conn.execute(
        f"SELECT MAX({qident(integer_pk)}) FROM {qident(table)}"
    ).fetchone()[0]
    return {
        "column": integer_pk,
        "max_value": value,
        "warning": "max primary key is a quick size diagnostic, not an exact row count",
    }


def inspect_table(conn: sqlite3.Connection, table: str) -> dict[str, Any]:
    return {
        "columns": table_columns(conn, table),
        "indexes": table_indexes(conn, table),
        "max_integer_pk": max_integer_pk(conn, table),
        "sample_rows": sample_rows(conn, table),
    }


def recent_price_bar_probe(
    conn: sqlite3.Connection, sample_limit: int = 5000
) -> dict[str, Any]:
    columns = {c["name"] for c in table_columns(conn, "price_bars")}
    wanted = [
        c
        for c in (
            "price_bar_id",
            "asset_id",
            "timestamp",
            "interval",
            "session_id",
            "trading_day",
            "source",
            "is_adjusted",
        )
        if c in columns
    ]
    if not wanted:
        return {"available": False, "reason": "no recognized price_bar columns"}

    order = " ORDER BY price_bar_id DESC" if "price_bar_id" in columns else ""
    rows = list(
        conn.execute(
            f"SELECT {','.join(qident(c) for c in wanted)} FROM price_bars{order} LIMIT ?",
            (int(sample_limit),),
        )
    )
    interval_counts = (
        Counter(str(r["interval"]) for r in rows if r["interval"] is not None)
        if "interval" in wanted
        else Counter()
    )
    source_counts = (
        Counter(str(r["source"]) for r in rows if r["source"] is not None)
        if "source" in wanted
        else Counter()
    )
    timestamps = [
        str(r["timestamp"])
        for r in rows
        if "timestamp" in wanted and r["timestamp"] is not None
    ]
    return {
        "available": True,
        "sample_is_exhaustive": False,
        "sample_rule": (
            f"latest {sample_limit} rows by price_bar_id"
            if "price_bar_id" in columns
            else f"first {sample_limit} rows without a stable recency key"
        ),
        "sample_rows": len(rows),
        "interval_counts": dict(interval_counts),
        "source_counts_top10": dict(source_counts.most_common(10)),
        "sample_timestamp_min": min(timestamps) if timestamps else None,
        "sample_timestamp_max": max(timestamps) if timestamps else None,
        "warning": (
            "This probe is intentionally cheap. Absence of an interval here does not prove it is absent from the full database."
        ),
    }


def deep_price_bar_scan(conn: sqlite3.Connection) -> dict[str, Any]:
    columns = {c["name"] for c in table_columns(conn, "price_bars")}
    required = {"interval", "asset_id", "timestamp"}
    if not required.issubset(columns):
        return {
            "available": False,
            "reason": f"missing columns: {sorted(required - columns)}",
        }
    rows = conn.execute(
        """
        SELECT
            interval,
            COUNT(*) AS rows,
            COUNT(DISTINCT asset_id) AS assets,
            MIN(timestamp) AS min_timestamp,
            MAX(timestamp) AS max_timestamp
        FROM price_bars
        GROUP BY interval
        ORDER BY interval
        """
    ).fetchall()
    return {
        "available": True,
        "expensive_full_scan": True,
        "by_interval": [
            {
                "interval": str(r["interval"]),
                "rows": int(r["rows"]),
                "assets": int(r["assets"]),
                "min_timestamp": r["min_timestamp"],
                "max_timestamp": r["max_timestamp"],
            }
            for r in rows
        ],
    }


def inspect_source(path: Path, deep_price_bars: bool = False) -> dict[str, Any]:
    out: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "opened_read_only": False,
    }
    if not path.exists():
        return out

    with connect_read_only(path) as conn:
        out["opened_read_only"] = True
        tables = table_names(conn)
        table_set = set(tables)
        out["tables_present"] = tables
        out["required_tables"] = {
            table: table in table_set for table in REQUIRED_SOURCE_TABLES
        }
        out["key_tables"] = {
            table: inspect_table(conn, table)
            for table in KEY_SOURCE_TABLES
            if table in table_set
        }
        if "price_bars" in table_set:
            out["recent_price_bar_probe"] = recent_price_bar_probe(conn)
            if deep_price_bars:
                out["deep_price_bar_scan"] = deep_price_bar_scan(conn)
    return out


def decode_jsonish(value: Any) -> Any:
    if value is None:
        return None
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return value


def read_core_metadata(path: Path) -> dict[str, Any]:
    out: dict[str, Any] = {
        "path": str(path),
        "exists": path.exists(),
        "opened_read_only": False,
    }
    if not path.exists():
        return out

    with connect_read_only(path) as conn:
        out["opened_read_only"] = True
        tables = set(table_names(conn))
        out["tables"] = sorted(tables)
        if "build_metadata" not in tables:
            out["build_metadata_present"] = False
            return out
        out["build_metadata_present"] = True
        rows = conn.execute("SELECT key,value_json FROM build_metadata").fetchall()
        metadata = {str(r["key"]): decode_jsonish(r["value_json"]) for r in rows}
        out["metadata"] = metadata

    config = metadata.get("config")
    checks: dict[str, dict[str, Any]] = {}
    if isinstance(config, dict):
        for key, expected in EXPECTED_CORE_METADATA.items():
            observed = config.get(key)
            checks[key] = {
                "expected": expected,
                "observed": observed,
                "match": observed == expected,
            }
    else:
        checks["config"] = {
            "expected": "JSON object",
            "observed": type(config).__name__,
            "match": False,
        }

    source_db = metadata.get("source_db")
    source_db_name = Path(str(source_db)).name if source_db else None
    checks["source_db_basename"] = {
        "expected": "market_data_v2.db",
        "observed": source_db_name,
        "match": source_db_name == "market_data_v2.db",
    }
    out["contract_checks"] = checks
    out["contract_matches"] = bool(checks) and all(
        bool(item.get("match")) for item in checks.values()
    )
    return out


def validate_temporal_config(payload: dict[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    hc = payload.get("horizon_contract") or {}
    guards = payload.get("guards") or {}

    expected_domain = {
        "minimum_sessions": 1,
        "maximum_sessions": 252,
        "unit": "eligible_exchange_sessions",
        "integer_only": True,
    }
    if hc.get("tau_domain") != expected_domain:
        errors.append("tau_domain must be the integer exchange-session domain 1..252")
    expected_strategies = [
        "configured_sparse",
        "configured_plus",
        "dense_all",
    ]
    if hc.get("supported_materialization_strategies") != expected_strategies:
        errors.append(
            "supported materialization strategies must be configured_sparse/configured_plus/dense_all"
        )
    if hc.get("default_materialization_strategy") != "configured_sparse":
        errors.append("configured_sparse must remain the default V001 strategy")

    existing = [int(x) for x in hc.get("existing_evaluation_sessions", [])]
    train = [int(x) for x in hc.get("training_anchor_sessions", [])]
    holdout = [int(x) for x in hc.get("temporal_generalization_holdout_sessions", [])]
    materialized = [int(x) for x in hc.get("materialized_sessions", [])]

    for name, values in (
        ("existing_evaluation_sessions", existing),
        ("training_anchor_sessions", train),
        ("temporal_generalization_holdout_sessions", holdout),
        ("materialized_sessions", materialized),
    ):
        if len(values) != len(set(values)):
            errors.append(f"{name} contains duplicates")
        if any(v <= 0 for v in values):
            errors.append(f"{name} must contain positive session counts")

    overlap = sorted(set(train) & set(holdout))
    if overlap:
        errors.append(f"training/holdout horizons overlap: {overlap}")

    expected_materialized = sorted(set(train) | set(holdout))
    if sorted(materialized) != expected_materialized:
        errors.append(
            "materialized_sessions must equal training_anchor_sessions union temporal_generalization_holdout_sessions"
        )

    missing_existing = sorted(set(existing) - set(materialized))
    if missing_existing:
        errors.append(f"existing evaluation horizons missing: {missing_existing}")

    maximum = int(hc.get("maximum_sessions", 0) or 0)
    if maximum != 252:
        errors.append("maximum_sessions must be frozen at 252 for V001")
    if materialized and max(materialized) > maximum:
        errors.append("materialized horizon exceeds maximum_sessions")
    if 252 not in materialized:
        errors.append("H252 must be materialized for the one-year anchor")

    if guards.get("training_authorized") is not False:
        errors.append("training_authorized must be false at this stage")
    if guards.get("v009_artifacts_loaded_or_modified") is not False:
        errors.append("V009 artifacts must remain untouched")
    if guards.get("source_market_db_mutation_allowed") is not False:
        errors.append("source market DB mutation must be forbidden")
    if guards.get("market_v003_core_mutation_allowed") is not False:
        errors.append("Market V003 Core mutation must be forbidden")

    return {
        "valid": not errors,
        "errors": errors,
        "training_anchor_sessions": train,
        "temporal_generalization_holdout_sessions": holdout,
        "materialized_sessions": materialized,
        "tau_domain": hc.get("tau_domain"),
        "default_materialization_strategy": hc.get("default_materialization_strategy"),
    }


def read_config(path: Path) -> tuple[dict[str, Any], dict[str, Any]]:
    if not path.exists():
        return {}, {"valid": False, "errors": [f"missing config: {path}"]}
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        return {}, {"valid": False, "errors": ["config root must be an object"]}
    return payload, validate_temporal_config(payload)


def gate_status(
    source: dict[str, Any],
    core: dict[str, Any],
    config_validation: dict[str, Any],
) -> tuple[str, list[str]]:
    reasons: list[str] = []
    if not source.get("exists"):
        reasons.append("canonical market source DB is missing")
    elif not source.get("opened_read_only"):
        reasons.append("canonical market source DB could not be opened read-only")

    required = source.get("required_tables") or {}
    missing = sorted(k for k, present in required.items() if not present)
    if missing:
        reasons.append(f"required source tables missing: {missing}")

    if not core.get("exists"):
        reasons.append("Market V003 Core DB is missing")
    elif not core.get("contract_matches"):
        reasons.append("Market V003 Core build metadata does not match frozen expectations")

    if not config_validation.get("valid"):
        reasons.append("Temporal Dataset V001 config validation failed")

    if reasons:
        return "REVIEW_REQUIRED", reasons
    return "READY_FOR_SOURCE_SCHEMA_REVIEW", []


def build_report(
    source_db: Path,
    core_db: Path,
    config_path: Path,
    *,
    deep_price_bars: bool = False,
) -> dict[str, Any]:
    payload, config_validation = read_config(config_path)
    source = inspect_source(source_db, deep_price_bars=deep_price_bars)
    core = read_core_metadata(core_db)
    status, reasons = gate_status(source, core, config_validation)
    return {
        "version": "temporal_source_audit_v001",
        "read_only": True,
        "status": status,
        "review_reasons": reasons,
        "source": source,
        "core": core,
        "temporal_dataset_config": {
            "path": str(config_path),
            "version": payload.get("version"),
            "status": payload.get("status"),
            "validation": config_validation,
        },
        "next_gate": {
            "if_ready": (
                "Design the idempotent Temporal Dataset V001 materializer, first requiring exact H1/H3/H5/H10 parity and then measuring long-horizon corporate-action overlap before training."
            ),
            "training_authorized": False,
            "materialization_authorized_by_this_audit": False,
            "deep_price_bar_scan_was_requested": bool(deep_price_bars),
        },
        "claim_boundary": (
            "This audit establishes source/schema/contract readiness only. It does not create labels, train a model, validate intraday skill, authorize long-horizon raw-close training, modify V009, or create a coherent path."
        ),
    }


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Read-only source/schema audit for Temporal Dataset V001"
    )
    parser.add_argument("--source-db", default=str(DEFAULT_SOURCE))
    parser.add_argument("--core-db", default=str(DEFAULT_CORE))
    parser.add_argument("--config", default=str(DEFAULT_CONFIG))
    parser.add_argument("--output", default=str(DEFAULT_OUTPUT))
    parser.add_argument(
        "--deep-price-bars",
        action="store_true",
        help=(
            "Run a full GROUP BY scan of price_bars by interval. This may be expensive on the ~29 GB source DB and is intentionally OFF by default."
        ),
    )
    args = parser.parse_args()

    report = build_report(
        Path(args.source_db),
        Path(args.core_db),
        Path(args.config),
        deep_price_bars=bool(args.deep_price_bars),
    )
    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(report, indent=2, ensure_ascii=False) + "\n",
        encoding="utf-8",
    )
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nWROTE {output}")


if __name__ == "__main__":
    main()

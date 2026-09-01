from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import tempfile
from contextlib import closing
from pathlib import Path
from typing import Any, Iterable, Mapping


VERSION = "public_information_semantics_audit_v001"
MODEL_VISIBILITY = (
    "BLOCKED_UNTIL_SEPARATE_POINT_IN_TIME_MATERIALIZER_AND_"
    "PREREGISTERED_INCREMENT_TEST"
)


class SemanticsAuditError(RuntimeError):
    """A structural, isolation or reproducibility gate failed."""


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _jsonable(value),
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(value: bytes) -> str:
    return hashlib.sha256(value).hexdigest()


def sha256_file(path: Path, chunk_size: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_size)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def resolve_path(root: Path, value: str | Path) -> Path:
    path = Path(value)
    if not path.is_absolute():
        path = root / path
    return path.resolve(strict=False)


def _atomic_json(path: Path, value: Mapping[str, Any]) -> str:
    payload = json.dumps(
        _jsonable(value), indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False
    ).encode("utf-8") + b"\n"
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temporary = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temporary, path)
    return sha256_bytes(payload)


def _readonly_sqlite(path: Path) -> sqlite3.Connection:
    uri = f"file:{path.as_posix()}?mode=ro"
    connection = sqlite3.connect(uri, uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _file_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    stat = path.stat()
    return {
        "path": str(path),
        "exists": True,
        "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _database_state(path: Path) -> list[dict[str, Any]]:
    return [_file_state(path)] + [
        _file_state(Path(str(path) + suffix)) for suffix in ("-wal", "-shm")
    ]


def _sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _rows(cursor: Any) -> list[dict[str, Any]]:
    names = [str(item[0]) for item in cursor.description]
    return [_jsonable(dict(zip(names, row))) for row in cursor.fetchall()]


def _one(cursor: Any) -> dict[str, Any]:
    rows = _rows(cursor)
    if len(rows) != 1:
        raise SemanticsAuditError(f"expected one row, observed {len(rows)}")
    return rows[0]


def validate_config(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if config.get("version") != VERSION:
        errors.append("version mismatch")
    if config.get("training_authorized") is not False:
        errors.append("training_authorized must remain false")
    if config.get("materialization_authorized") is not False:
        errors.append("materialization_authorized must remain false")
    if config.get("feature_visibility") != MODEL_VISIBILITY:
        errors.append("feature_visibility mismatch")
    policies = config.get("bar_contract", {})
    if policies.get("cross_source_policy") != "PRESERVE_BOTH_NO_MEDIAN_NO_OVERWRITE":
        errors.append("cross-source median/overwrite is forbidden")
    if policies.get("volume_policy") != "REPORT_ONLY_NEVER_BLEND":
        errors.append("cross-feed volume blending is forbidden")
    news = config.get("news_contract", {})
    if news.get("source_asymmetry_policy") != (
        "MEASURE_AND_PRESERVE_NOT_A_BLOCKER_NOT_AN_INDEPENDENCE_CLAIM"
    ):
        errors.append("source asymmetry must be measured rather than blocked")
    if news.get("publisher_reliability_policy") != "NOT_HARDCODED_NOT_SCORED_BY_THIS_AUDIT":
        errors.append("publisher reliability cannot be hardcoded")
    paths = config.get("paths", {})
    required = {
        "intake_config",
        "intake_catalog_db",
        "market_db",
        "core_db",
        "graph_identity_db",
        "report_dir",
    }
    missing = sorted(required - set(paths))
    if missing:
        errors.append(f"missing paths: {missing}")
    # Inspect only operational locations.  The guard list necessarily contains
    # the forbidden words themselves and must not make its own contract fail.
    configured_text = json.dumps(
        {"paths": config.get("paths", {}), "snapshots": config.get("snapshots", {})},
        sort_keys=True,
    ).lower()
    for token in config.get("guards", {}).get("forbidden_path_tokens", []):
        if token.lower() in configured_text:
            errors.append(f"V009/holdout token configured: {token}")
    report_dir = resolve_path(root, paths.get("report_dir", "reports/invalid"))
    for forbidden in config.get("guards", {}).get("forbidden_outputs", []):
        forbidden_path = resolve_path(root, forbidden)
        try:
            report_dir.relative_to(forbidden_path)
            errors.append(f"report directory intersects protected path: {forbidden_path}")
        except ValueError:
            pass
    return {
        "valid": not errors,
        "errors": errors,
        "training_authorized": False,
        "materialization_authorized": False,
        "feature_visibility": MODEL_VISIBILITY,
    }


def load_config(root: Path, path: Path) -> dict[str, Any]:
    config = read_json(path)
    result = validate_config(root, config)
    if not result["valid"]:
        raise SemanticsAuditError("invalid config: " + "; ".join(result["errors"]))
    return config


def _latest_snapshot(
    root: Path, config: Mapping[str, Any], snapshot_name: str
) -> dict[str, Any]:
    spec = config["snapshots"][snapshot_name]
    catalog = resolve_path(root, config["paths"]["intake_catalog_db"])
    with closing(_readonly_sqlite(catalog)) as connection:
        row = connection.execute(
            """
            SELECT snapshot_id,manifest_path,resolved_revision,manifest_sha256,
                   selected_file_count,selected_bytes
            FROM dataset_snapshots
            WHERE dataset_key=? AND profile_name=?
            ORDER BY last_verified_at_utc DESC,snapshot_id DESC LIMIT 1
            """,
            (spec["dataset_key"], spec["profile_name"]),
        ).fetchone()
        if row is None:
            raise SemanticsAuditError(
                f"no frozen snapshot for {spec['dataset_key']}/{spec['profile_name']}"
            )
        file_rows = connection.execute(
            """
            SELECT repo_path,size_bytes,lfs_sha256,local_path,status,
                   local_size_bytes,local_sha256
            FROM snapshot_files WHERE snapshot_id=? ORDER BY repo_path
            """,
            (row[0],),
        ).fetchall()
    files: list[dict[str, Any]] = []
    for file_row in file_rows:
        local_path = Path(file_row[3])
        observed_size = local_path.stat().st_size if local_path.exists() else None
        complete = (
            local_path.exists()
            and observed_size == int(file_row[1])
            and file_row[4] == "COMPLETE"
        )
        files.append(
            {
                "repo_path": file_row[0],
                "size_bytes": int(file_row[1]),
                "lfs_sha256": file_row[2],
                "local_path": str(local_path),
                "catalog_status": file_row[4],
                "catalog_local_size_bytes": file_row[5],
                "catalog_local_sha256": file_row[6],
                "observed_size_bytes": observed_size,
                "complete": complete,
            }
        )
    if not files or not all(item["complete"] for item in files):
        incomplete = [item["repo_path"] for item in files if not item["complete"]]
        raise SemanticsAuditError(
            f"snapshot {row[0]} is incomplete; first paths: {incomplete[:5]}"
        )
    manifest_path = Path(row[1])
    manifest = read_json(manifest_path)
    if manifest.get("manifest_sha256") != row[3]:
        raise SemanticsAuditError(f"manifest/catalog hash mismatch for {row[0]}")
    return {
        "snapshot_name": snapshot_name,
        "dataset_key": spec["dataset_key"],
        "profile_name": spec["profile_name"],
        "snapshot_id": row[0],
        "manifest_path": str(manifest_path),
        "manifest_sha256": row[3],
        "resolved_revision": row[2],
        "selected_file_count": int(row[4]),
        "selected_bytes": int(row[5]),
        "files": files,
    }


def _parquet_scan(paths: Iterable[str]) -> str:
    values = ",".join(_sql_string(path) for path in paths)
    if not values:
        raise SemanticsAuditError("empty parquet source")
    return f"read_parquet([{values}], union_by_name=true, filename=true)"


def _input_state(
    root: Path,
    config: Mapping[str, Any],
    snapshots: Mapping[str, Mapping[str, Any]],
) -> dict[str, Any]:
    databases = {
        name: _database_state(resolve_path(root, config["paths"][name]))
        for name in ("intake_catalog_db", "market_db", "core_db", "graph_identity_db")
    }
    parquet = {
        name: [_file_state(Path(item["local_path"])) for item in snapshot["files"]]
        for name, snapshot in snapshots.items()
    }
    return {"databases": databases, "parquet": parquet}


def _connect(
    root: Path,
    config: Mapping[str, Any],
    snapshots: Mapping[str, Mapping[str, Any]],
    temp_directory: Path,
):
    try:
        import duckdb
    except ImportError as exc:
        raise SemanticsAuditError(
            "DuckDB is required; install requirements-information-intake.txt"
        ) from exc
    connection = duckdb.connect(":memory:")
    # A 531M-row group-by can otherwise consume all host RAM.  A bounded
    # memory budget plus an audit-local spill directory makes the workload
    # predictable and leaves no persistent derived dataset behind.
    connection.execute("SET threads=2")
    connection.execute("SET memory_limit='6GB'")
    connection.execute(f"SET temp_directory={_sql_string(temp_directory)}")
    connection.execute("SET preserve_insertion_order=false")
    for alias, key in (
        ("market", "market_db"),
        ("core", "core_db"),
        ("graph_identity", "graph_identity_db"),
    ):
        path = resolve_path(root, config["paths"][key])
        connection.execute(
            f"ATTACH {_sql_string(path)} AS {alias} (TYPE SQLITE, READ_ONLY)"
        )
    bars = _parquet_scan(item["local_path"] for item in snapshots["bars"]["files"])
    news = _parquet_scan(item["local_path"] for item in snapshots["news"]["files"])
    connection.execute(f"CREATE TEMP VIEW raw_bars AS SELECT * FROM {bars}")
    connection.execute(f"CREATE TEMP VIEW raw_news AS SELECT * FROM {news}")
    connection.execute(
        """
        CREATE TEMP VIEW core_assets AS
        SELECT asset_id,ticker,ANY_VALUE(sector) AS sector,
               MIN(trading_day) AS core_min_day,MAX(trading_day) AS core_max_day,
               COUNT(*) AS core_state_rows
        FROM core.market_daily_v003_states
        GROUP BY asset_id,ticker
        """
    )
    connection.execute(
        """
        CREATE TEMP VIEW news_parsed AS
        WITH typed AS (
          SELECT *,TRY_CAST(extra_fields AS JSON) AS extra_json,
                 TRY_CAST(date AS TIMESTAMPTZ) AS published_proxy_utc
          FROM raw_news
        )
        SELECT *,
          json_extract_string(extra_json,'$.dataset') AS collection_source,
          json_extract_string(extra_json,'$.dataset_source') AS dataset_source,
          json_extract_string(extra_json,'$.publisher') AS publisher_raw,
          json_extract_string(extra_json,'$.source') AS source_raw,
          json_extract_string(extra_json,'$.url') AS url_raw,
          json_extract_string(extra_json,'$.time_precision') AS time_precision,
          json_extract_string(extra_json,'$.text_type') AS text_type,
          json_extract_string(extra_json,'$.tz_hint') AS tz_hint,
          TRY_CAST(json_extract_string(extra_json,'$.date_trading') AS TIMESTAMPTZ)
            AS date_trading_proxy_utc,
          lower(regexp_extract(
            COALESCE(json_extract_string(extra_json,'$.url'),''),
            '^https?://(?:www\\.)?([^/:?#]+)',1
          )) AS normalized_url_domain,
          regexp_replace(
            lower(trim(COALESCE(text,''))),
            '[^[:alnum:]]+',' ','g'
          ) AS normalized_text
        FROM typed
        """
    )
    return connection


def build_plan(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    snapshots = {
        name: _latest_snapshot(root, config, name) for name in ("bars", "news")
    }
    state = _input_state(root, config, snapshots)
    fingerprint_basis = {
        "version": VERSION,
        "config_sha256": sha256_bytes(canonical_json_bytes(config)),
        "snapshots": {
            name: {
                key: snapshot[key]
                for key in (
                    "dataset_key",
                    "profile_name",
                    "snapshot_id",
                    "manifest_sha256",
                    "resolved_revision",
                    "selected_file_count",
                    "selected_bytes",
                )
            }
            for name, snapshot in snapshots.items()
        },
        "input_state": state,
    }
    return {
        "version": VERSION,
        "status": "READY_FOR_READ_ONLY_SEMANTICS_AUDIT",
        "config_validation": validate_config(root, config),
        "input_fingerprint": sha256_bytes(canonical_json_bytes(fingerprint_basis)),
        "inputs": fingerprint_basis,
        "semantics": {
            "fnspid": (
                "Collection lineage is distinct from document publisher/domain. "
                "Source asymmetry is measured but is not a rejection gate."
            ),
            "bars": (
                "Alpaca-derived bars remain feed-unknown and source-specific; "
                "Yahoo is an independent comparison, never a median component."
            ),
            "identity": (
                "Exact Core ticker links are current-symbol proxies unless a "
                "valid_from/valid_to identifier history proves historical identity."
            ),
        },
        "planned_reports": list(config["outputs"]),
        "training_authorized": False,
        "materialization_authorized": False,
        "feature_visibility": MODEL_VISIBILITY,
        "v009_interaction": "NONE",
    }


def _build_intraday_daily(connection: Any, config: Mapping[str, Any]) -> None:
    timezone = config["bar_contract"]["exchange_timezone"]
    pre_start, pre_end = config["bar_contract"]["session_windows_local"]["premarket"]
    rth_start, rth_end = config["bar_contract"]["session_windows_local"]["rth"]
    after_start, after_end = config["bar_contract"]["session_windows_local"]["afterhours"]
    connection.execute(
        f"""
        CREATE TEMP TABLE intraday_daily AS
        WITH localized AS (
          SELECT b.ticker,b.timestamp,b.open,b.high,b.low,b.close,b.volume,
                 b.trade_count,b.vol_weighted_avg_price,
                 timezone({_sql_string(timezone)},b.timestamp) AS local_ts,
                 CAST(timezone({_sql_string(timezone)},b.timestamp) AS TIME) AS local_time,
                 CAST(timezone({_sql_string(timezone)},b.timestamp) AS DATE) AS local_day,
                 EXTRACT(isodow FROM timezone({_sql_string(timezone)},b.timestamp)) AS local_isodow
          FROM raw_bars b
          INNER JOIN core_assets c ON upper(trim(b.ticker))=upper(c.ticker)
        ), rth AS (
          SELECT * FROM localized
          WHERE local_isodow BETWEEN 1 AND 5
            AND local_time>=TIME {_sql_string(rth_start)}
            AND local_time<TIME {_sql_string(rth_end)}
        )
        SELECT upper(trim(ticker)) AS ticker,local_day AS trading_day,
          COUNT(*) AS minute_rows,
          COUNT(DISTINCT timestamp) AS distinct_minutes,
          MIN(timestamp) AS first_bar_utc,MAX(timestamp) AS last_bar_utc,
          arg_min(open,timestamp) AS first_minute_open,
          arg_min(close,timestamp) AS first_minute_close,
          arg_min(vol_weighted_avg_price,timestamp) AS first_minute_vwap,
          MAX(high) AS rth_high,MIN(low) AS rth_low,
          arg_max(close,timestamp) AS rth_close,
          SUM(volume) AS rth_volume,SUM(trade_count) AS rth_trade_count,
          CASE WHEN SUM(CASE WHEN local_time<TIME '09:35:00' THEN volume ELSE 0 END)>0
            THEN SUM(CASE WHEN local_time<TIME '09:35:00'
                          THEN vol_weighted_avg_price*volume ELSE 0 END)
                 /SUM(CASE WHEN local_time<TIME '09:35:00' THEN volume ELSE 0 END)
            ELSE NULL END AS first_five_minute_vwap,
          SUM(CASE WHEN local_time<TIME '09:35:00' THEN 1 ELSE 0 END)
            AS first_five_minute_rows
        FROM rth GROUP BY ticker,local_day
        """
    )
    connection.execute(
        """
        CREATE TEMP VIEW yahoo_latest AS
        WITH ranked AS (
          SELECT o.asset_id,o.trading_day,v.open,v.high,v.low,v.close,v.volume,
                 o.observation_sequence,o.observed_at,
                 ROW_NUMBER() OVER (
                   PARTITION BY o.asset_id,o.trading_day
                   ORDER BY o.observation_sequence DESC,o.observed_at DESC,
                            o.price_observation_id DESC
                 ) AS row_rank
          FROM market.price_bar_observations o
          JOIN market.price_bar_versions v
            ON v.price_bar_version_id=o.price_bar_version_id
          WHERE o.source_id='yahoo_finance'
        )
        SELECT r.*,a.ticker FROM ranked r
        JOIN market.assets a USING(asset_id)
        WHERE row_rank=1
        """
    )
    connection.execute(
        """
        CREATE TEMP VIEW matched_daily AS
        SELECT d.*,c.asset_id,y.open AS yahoo_open,y.high AS yahoo_high,
               y.low AS yahoo_low,y.close AS yahoo_close,y.volume AS yahoo_volume
        FROM intraday_daily d
        JOIN core_assets c USING(ticker)
        JOIN yahoo_latest y
          ON y.asset_id=c.asset_id AND y.trading_day=CAST(d.trading_day AS VARCHAR)
        """
    )


def _bars_coverage_report(connection: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    overall = _one(
        connection.execute(
            """
            SELECT COUNT(*) AS rows,MIN(timestamp) AS min_timestamp,
                   MAX(timestamp) AS max_timestamp,
                   COUNT(DISTINCT upper(trim(ticker))) AS distinct_tickers,
                   APPROX_COUNT_DISTINCT(hash(upper(trim(ticker)),timestamp))
                     AS approx_distinct_ticker_timestamp,
                   SUM(open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL)
                     AS null_ohlc_rows,
                   SUM(open<=0 OR high<=0 OR low<=0 OR close<=0)
                     AS nonpositive_ohlc_rows,
                   SUM(high<GREATEST(open,close,low) OR low>LEAST(open,close,high))
                     AS ohlc_envelope_violations,
                   SUM(volume IS NULL OR volume<0) AS invalid_volume_rows,
                   SUM(trade_count IS NULL OR trade_count<0) AS invalid_trade_count_rows
            FROM raw_bars
            """
        )
    )
    tickers = _rows(
        connection.execute(
            """
            SELECT upper(trim(b.ticker)) AS ticker,COUNT(*) AS rows,
                   MIN(timestamp) AS min_timestamp,MAX(timestamp) AS max_timestamp,
                   COUNT(DISTINCT CAST(timezone('America/New_York',timestamp) AS DATE))
                     AS local_calendar_days,
                   MAX(c.asset_id) AS core_asset_id,
                   CASE WHEN MAX(c.asset_id) IS NULL THEN 'NO_EXACT_CORE_MATCH'
                        ELSE 'EXACT_CURRENT_SYMBOL_PROXY' END AS identity_status
            FROM raw_bars b LEFT JOIN core_assets c
              ON upper(trim(b.ticker))=upper(c.ticker)
            GROUP BY upper(trim(b.ticker)) ORDER BY ticker
            """
        )
    )
    matched = sum(1 for item in tickers if item["core_asset_id"] is not None)
    return {
        "version": VERSION,
        "status": (
            "PASS_BARS_STRUCTURAL_COVERAGE_REVIEW_READY"
            if not any(
                int(overall[key] or 0)
                for key in (
                    "null_ohlc_rows",
                    "nonpositive_ohlc_rows",
                    "ohlc_envelope_violations",
                    "invalid_volume_rows",
                    "invalid_trade_count_rows",
                )
            )
            else "REVIEW_BARS_STRUCTURAL_ANOMALIES"
        ),
        "overall": overall,
        "core_exact_current_symbol_overlap": {
            "bar_tickers": len(tickers),
            "matched_bar_tickers": matched,
            "unmatched_bar_tickers": len(tickers) - matched,
            "core_assets": _one(connection.execute("SELECT COUNT(*) AS n FROM core_assets"))["n"],
            "mapping_semantics": config["identity_contract"]["primary_mapping"],
        },
        "ticker_coverage": tickers,
        "duplicate_identity_note": (
            "The full row count is compared with an approximate distinct hash. "
            "Exact duplicate checks are performed after the Core/RTH reduction; "
            "the approximation is not a small-duplicate proof."
        ),
        "canonical_status": "BLOCKED_PENDING_FEED_SESSION_ADJUSTMENT_AND_IDENTITY_REVIEW",
        "training_authorized": False,
    }


def _bars_session_report(connection: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    timezone = config["bar_contract"]["exchange_timezone"]
    windows = config["bar_contract"]["session_windows_local"]
    pre_start, pre_end = windows["premarket"]
    rth_start, rth_end = windows["rth"]
    after_start, after_end = windows["afterhours"]
    sessions = _rows(
        connection.execute(
            f"""
            WITH localized AS (
              SELECT timestamp,timezone({_sql_string(timezone)},timestamp) AS local_ts
              FROM raw_bars
            ), classified AS (
              SELECT *,CAST(local_ts AS TIME) AS local_time,
                EXTRACT(isodow FROM local_ts) AS local_isodow,
                CASE
                  WHEN EXTRACT(isodow FROM local_ts) IN (6,7) THEN 'weekend_local'
                  WHEN CAST(local_ts AS TIME)>=TIME {_sql_string(pre_start)}
                   AND CAST(local_ts AS TIME)<TIME {_sql_string(pre_end)} THEN 'premarket'
                  WHEN CAST(local_ts AS TIME)>=TIME {_sql_string(rth_start)}
                   AND CAST(local_ts AS TIME)<TIME {_sql_string(rth_end)} THEN 'rth'
                  WHEN CAST(local_ts AS TIME)>=TIME {_sql_string(after_start)}
                   AND CAST(local_ts AS TIME)<TIME {_sql_string(after_end)} THEN 'afterhours'
                  ELSE 'outside_standard_extended'
                END AS session_class
              FROM localized
            )
            SELECT session_class,COUNT(*) AS rows,MIN(timestamp) AS min_timestamp,
                   MAX(timestamp) AS max_timestamp,
                   SUM(EXTRACT(second FROM local_ts)<>0) AS nonzero_second_rows
            FROM classified GROUP BY session_class ORDER BY session_class
            """
        )
    )
    daily = _one(
        connection.execute(
            f"""
            SELECT COUNT(*) AS core_rth_asset_days,
                   COUNT(DISTINCT ticker) AS core_rth_tickers,
                   MIN(trading_day) AS min_trading_day,MAX(trading_day) AS max_trading_day,
                   SUM(minute_rows={int(config['bar_contract']['expected_full_rth_minutes'])})
                     AS exact_390_minute_days,
                   SUM(minute_rows<{int(config['bar_contract']['expected_full_rth_minutes'])})
                     AS below_390_minute_days,
                   SUM(minute_rows>{int(config['bar_contract']['expected_full_rth_minutes'])})
                     AS above_390_minute_days,
                   SUM(minute_rows<>distinct_minutes) AS duplicate_minute_asset_days,
                   MIN(minute_rows) AS minimum_minutes,MAX(minute_rows) AS maximum_minutes,
                   QUANTILE_CONT(minute_rows,0.5) AS median_minutes,
                   QUANTILE_CONT(minute_rows,0.05) AS p05_minutes,
                   QUANTILE_CONT(minute_rows,0.95) AS p95_minutes
            FROM intraday_daily
            """
        )
    )
    return {
        "version": VERSION,
        "status": "REVIEW_SESSION_AND_FEED_SEMANTICS_REQUIRED",
        "exchange_timezone": timezone,
        "timestamp_semantics": config["bar_contract"]["timestamp_semantics"],
        "session_windows_local": windows,
        "rows_by_session": sessions,
        "core_rth_daily_completeness": daily,
        "interpretation": (
            "RTH/extended classifications are deterministic clock diagnostics. "
            "They do not prove exchange eligibility, auction inclusion or feed identity."
        ),
        "training_authorized": False,
    }


def _difference_summary(connection: Any, expression: str) -> dict[str, Any]:
    return _one(
        connection.execute(
            f"""
            SELECT COUNT({expression}) AS rows,
                   QUANTILE_CONT({expression},0.50) AS p50_abs_pct,
                   QUANTILE_CONT({expression},0.90) AS p90_abs_pct,
                   QUANTILE_CONT({expression},0.95) AS p95_abs_pct,
                   QUANTILE_CONT({expression},0.99) AS p99_abs_pct,
                   MAX({expression}) AS max_abs_pct
            FROM matched_daily
            """
        )
    )


def _bars_reconciliation_report(connection: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    expressions = {
        "open": "ABS(100.0*(first_minute_open/yahoo_open-1.0))",
        "high": "ABS(100.0*(rth_high/yahoo_high-1.0))",
        "low": "ABS(100.0*(rth_low/yahoo_low-1.0))",
        "close": "ABS(100.0*(rth_close/yahoo_close-1.0))",
    }
    summaries = {
        name: _difference_summary(connection, expression)
        for name, expression in expressions.items()
    }
    threshold_rows = []
    for threshold in config["bar_contract"]["price_difference_thresholds_pct"]:
        row = {"threshold_pct": threshold}
        for name, expression in expressions.items():
            row[f"{name}_rows_above"] = _one(
                connection.execute(
                    f"SELECT SUM({expression}>{float(threshold)}) AS n FROM matched_daily"
                )
            )["n"]
        threshold_rows.append(row)
    returns = _one(
        connection.execute(
            """
            WITH ordered AS (
              SELECT *,LAG(rth_close) OVER(PARTITION BY ticker ORDER BY trading_day)
                       AS prev_alpaca_close,
                       LAG(yahoo_close) OVER(PARTITION BY ticker ORDER BY trading_day)
                       AS prev_yahoo_close
              FROM matched_daily
            ), differences AS (
              SELECT ABS(
                100.0*(rth_close/prev_alpaca_close-1.0)
                -100.0*(yahoo_close/prev_yahoo_close-1.0)
              ) AS abs_return_difference_pct
              FROM ordered WHERE prev_alpaca_close>0 AND prev_yahoo_close>0
            )
            SELECT COUNT(*) AS rows,
                   QUANTILE_CONT(abs_return_difference_pct,0.5) AS p50_abs_pct_points,
                   QUANTILE_CONT(abs_return_difference_pct,0.95) AS p95_abs_pct_points,
                   QUANTILE_CONT(abs_return_difference_pct,0.99) AS p99_abs_pct_points,
                   MAX(abs_return_difference_pct) AS max_abs_pct_points
            FROM differences
            """
        )
    )
    worst = _rows(
        connection.execute(
            """
            SELECT ticker,trading_day,first_minute_open,yahoo_open,rth_close,yahoo_close,
                   ABS(100.0*(rth_close/yahoo_close-1.0)) AS close_abs_difference_pct,
                   minute_rows
            FROM matched_daily
            WHERE yahoo_close>0 ORDER BY close_abs_difference_pct DESC LIMIT 100
            """
        )
    )
    support = _one(
        connection.execute(
            """
            SELECT COUNT(*) AS matched_asset_days,COUNT(DISTINCT ticker) AS matched_tickers,
                   MIN(trading_day) AS min_day,MAX(trading_day) AS max_day
            FROM matched_daily
            """
        )
    )
    return {
        "version": VERSION,
        "status": "REVIEW_CROSS_SOURCE_SEMANTICS_REQUIRED",
        "support": support,
        "absolute_price_difference_pct": summaries,
        "threshold_counts": threshold_rows,
        "close_to_close_return_difference": returns,
        "largest_close_differences": worst,
        "source_policy": config["bar_contract"]["cross_source_policy"],
        "volume_policy": config["bar_contract"]["volume_policy"],
        "interpretation": (
            "Yahoo Open is compared with the first observed RTH minute open, not "
            "declared identical to an official auction. Close-return agreement is "
            "more informative than raw level agreement across adjustment conventions."
        ),
        "canonical_primary_status": "BLOCKED_PENDING_FEED_AND_ADJUSTMENT_PROVENANCE",
        "training_authorized": False,
    }


def _opening_report(connection: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    threshold = float(config["bar_contract"]["opening_gap_threshold_pct"])
    connection.execute(
        """
        CREATE TEMP VIEW opening_metrics AS
        WITH ordered AS (
          SELECT *,LAG(rth_close) OVER(PARTITION BY ticker ORDER BY trading_day)
                     AS previous_rth_close,
                   LAG(yahoo_close) OVER(PARTITION BY ticker ORDER BY trading_day)
                     AS previous_yahoo_close
          FROM matched_daily
        )
        SELECT *,
          100.0*(first_minute_open/previous_rth_close-1.0) AS alpaca_opening_gap_pct,
          100.0*(first_minute_close/first_minute_open-1.0) AS first_minute_move_pct,
          100.0*(first_five_minute_vwap/first_minute_open-1.0) AS first_five_vwap_move_pct,
          100.0*(yahoo_open/previous_yahoo_close-1.0) AS yahoo_opening_gap_pct,
          100.0*(first_minute_open/yahoo_open-1.0) AS first_minute_vs_yahoo_open_pct
        FROM ordered
        WHERE previous_rth_close>0 AND previous_yahoo_close>0
          AND first_minute_open>0 AND yahoo_open>0
        """
    )
    metrics = {}
    for column in (
        "alpaca_opening_gap_pct",
        "first_minute_move_pct",
        "first_five_vwap_move_pct",
        "yahoo_opening_gap_pct",
        "first_minute_vs_yahoo_open_pct",
    ):
        metrics[column] = _one(
            connection.execute(
                f"""
                SELECT COUNT({column}) AS rows,QUANTILE_CONT({column},0.01) AS p01,
                       QUANTILE_CONT({column},0.05) AS p05,
                       QUANTILE_CONT({column},0.50) AS p50,
                       QUANTILE_CONT({column},0.95) AS p95,
                       QUANTILE_CONT({column},0.99) AS p99,
                       MIN({column}) AS minimum,MAX({column}) AS maximum,
                       SUM(ABS({column})>={threshold}) AS abs_ge_threshold_rows
                FROM opening_metrics
                """
            )
        )
    action_overlap = _one(
        connection.execute(
            f"""
            WITH actions AS (
              SELECT DISTINCT asset_id,effective_trading_day
              FROM market.corporate_action_versions WHERE is_present=1
            )
            SELECT COUNT(*) AS metric_rows,
              SUM(a.asset_id IS NOT NULL) AS action_day_rows,
              SUM(ABS(o.alpaca_opening_gap_pct)>={threshold}) AS extreme_gap_rows,
              SUM(ABS(o.alpaca_opening_gap_pct)>={threshold} AND a.asset_id IS NOT NULL)
                AS extreme_gap_action_day_rows,
              SUM(ABS(o.first_minute_move_pct)>={threshold}) AS extreme_after_open_rows
            FROM opening_metrics o
            LEFT JOIN actions a ON a.asset_id=o.asset_id
              AND a.effective_trading_day=CAST(o.trading_day AS VARCHAR)
            """
        )
    )
    extremes = _rows(
        connection.execute(
            """
            SELECT ticker,trading_day,previous_rth_close,first_minute_open,
                   first_minute_close,first_five_minute_vwap,yahoo_open,
                   alpaca_opening_gap_pct,first_minute_move_pct,
                   first_five_vwap_move_pct,first_minute_vs_yahoo_open_pct,
                   minute_rows
            FROM opening_metrics
            ORDER BY ABS(alpaca_opening_gap_pct) DESC LIMIT 100
            """
        )
    )
    return {
        "version": VERSION,
        "status": "PASS_OPENING_COMPONENTS_SEPARATED_REVIEW_REQUIRED",
        "definitions": {
            "overnight_gap": "100*(first_RTH_minute_open/previous_RTH_close-1)",
            "first_minute_move": "100*(first_RTH_minute_close/first_RTH_minute_open-1)",
            "first_five_vwap_move": "100*(RTH_5m_VWAP/first_RTH_minute_open-1)",
            "yahoo_gap_control": "100*(Yahoo_open/previous_Yahoo_close-1)",
            "official_auction": "NOT_OBSERVED_AS_A_SEPARATE_FIELD",
        },
        "threshold_pct": threshold,
        "metrics": metrics,
        "corporate_action_overlap": action_overlap,
        "largest_opening_gaps": extremes,
        "interpretation": (
            "A large overnight gap is not an instantaneous after-open move. "
            "The dataset cannot identify the official opening auction separately."
        ),
        "training_authorized": False,
    }


def _news_source_report(connection: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    overall = _one(
        connection.execute(
            """
            SELECT COUNT(*) AS rows,MIN(published_proxy_utc) AS min_date,
                   MAX(published_proxy_utc) AS max_date,
                   COUNT(DISTINCT collection_source) AS collection_sources,
                   COUNT(DISTINCT lower(trim(publisher_raw))) FILTER(
                     WHERE publisher_raw IS NOT NULL AND trim(publisher_raw)<>'')
                     AS normalized_publisher_values,
                   COUNT(DISTINCT normalized_url_domain) FILTER(
                     WHERE normalized_url_domain<>'') AS url_domains,
                   SUM(extra_json IS NULL) AS invalid_extra_fields_json,
                   SUM(text IS NULL OR trim(text)='') AS missing_text_rows,
                   SUM(url_raw IS NULL OR trim(url_raw)='') AS missing_url_rows,
                   SUM(publisher_raw IS NULL OR trim(publisher_raw)='')
                     AS missing_publisher_rows,
                   AVG(length(text)) AS mean_text_chars,
                   QUANTILE_CONT(length(text),0.5) AS median_text_chars,
                   QUANTILE_CONT(length(text),0.95) AS p95_text_chars
            FROM news_parsed
            """
        )
    )
    by_collection = _rows(
        connection.execute(
            """
            SELECT COALESCE(collection_source,'MISSING') AS collection_source,
                   COALESCE(dataset_source,'MISSING') AS dataset_source,
                   COUNT(*) AS rows,MIN(published_proxy_utc) AS min_date,
                   MAX(published_proxy_utc) AS max_date,
                   COUNT(DISTINCT lower(trim(publisher_raw))) FILTER(
                     WHERE publisher_raw IS NOT NULL AND trim(publisher_raw)<>'')
                     AS publisher_values,
                   COUNT(DISTINCT normalized_url_domain) FILTER(
                     WHERE normalized_url_domain<>'') AS url_domains,
                   SUM(json_extract(extra_json,'$.stocks') IS NOT NULL)
                     AS rows_with_stocks
            FROM news_parsed GROUP BY 1,2 ORDER BY rows DESC
            """
        )
    )
    top_n = int(config["news_contract"]["top_n_source_rows"])
    domains = _rows(
        connection.execute(
            f"""
            SELECT COALESCE(collection_source,'MISSING') AS collection_source,
                   normalized_url_domain AS domain,COUNT(*) AS rows,
                   COUNT(DISTINCT lower(trim(publisher_raw))) FILTER(
                     WHERE publisher_raw IS NOT NULL AND trim(publisher_raw)<>'')
                     AS publisher_values,
                   MIN(published_proxy_utc) AS min_date,MAX(published_proxy_utc) AS max_date
            FROM news_parsed WHERE normalized_url_domain<>''
            GROUP BY 1,2 ORDER BY rows DESC LIMIT {top_n}
            """
        )
    )
    publishers = _rows(
        connection.execute(
            f"""
            SELECT COALESCE(collection_source,'MISSING') AS collection_source,
                   trim(publisher_raw) AS publisher_raw,COUNT(*) AS rows,
                   COUNT(DISTINCT normalized_url_domain) FILTER(
                     WHERE normalized_url_domain<>'') AS url_domains,
                   MIN(published_proxy_utc) AS min_date,MAX(published_proxy_utc) AS max_date
            FROM news_parsed
            WHERE publisher_raw IS NOT NULL AND trim(publisher_raw)<>''
            GROUP BY 1,2 ORDER BY rows DESC LIMIT {top_n}
            """
        )
    )
    return {
        "version": VERSION,
        "status": "PASS_COLLECTION_PUBLISHER_DOMAIN_LINEAGE_SEPARATED",
        "overall": overall,
        "collections": by_collection,
        "top_document_domains": domains,
        "top_publisher_values": publishers,
        "fnspid_semantics": {
            "collection": "fnspid_news",
            "upstream": config["news_contract"]["fnspid_collection_path"],
            "declared_collection_route": config["news_contract"]["fnspid_declared_collection_route"],
            "publisher_interpretation": (
                "The preserved FNSPID publisher field can contain publisher, desk, "
                "author or email-like byline values. URL domain is the deterministic "
                "document source-family proxy; neither is a hardcoded reliability score."
            ),
        },
        "asymmetry_policy": config["news_contract"]["source_asymmetry_policy"],
        "rights_status": "LOCAL_RESEARCH_ONLY_NO_RAW_TEXT_REDISTRIBUTION",
        "training_authorized": False,
    }


def _news_time_report(connection: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    by_precision = _rows(
        connection.execute(
            """
            SELECT COALESCE(collection_source,'MISSING') AS collection_source,
                   COALESCE(time_precision,'MISSING') AS time_precision,
                   COALESCE(tz_hint,'MISSING') AS tz_hint,
                   COUNT(*) AS rows,
                   SUM(published_proxy_utc IS NULL) AS unparseable_rows,
                   SUM(published_proxy_utc IS NOT NULL
                       AND EXTRACT(hour FROM published_proxy_utc)=0
                       AND EXTRACT(minute FROM published_proxy_utc)=0
                       AND EXTRACT(second FROM published_proxy_utc)=0)
                     AS exact_midnight_utc_rows,
                   SUM(date_trading_proxy_utc IS NOT NULL) AS date_trading_proxy_rows,
                   MIN(published_proxy_utc) AS min_date,MAX(published_proxy_utc) AS max_date
            FROM news_parsed GROUP BY 1,2,3 ORDER BY rows DESC
            """
        )
    )
    tiers = _rows(
        connection.execute(
            """
            WITH tiered AS (
              SELECT CASE
                WHEN published_proxy_utc IS NULL THEN 'UNPARSEABLE_OR_MISSING'
                WHEN lower(COALESCE(time_precision,''))='minute'
                 AND EXTRACT(hour FROM published_proxy_utc)=0
                 AND EXTRACT(minute FROM published_proxy_utc)=0
                 AND EXTRACT(second FROM published_proxy_utc)=0
                  THEN 'SUSPECT_MIDNIGHT_MINUTE'
                WHEN lower(COALESCE(time_precision,''))='minute'
                  THEN 'HISTORICAL_MINUTE_PUBLICATION_PROXY'
                WHEN lower(COALESCE(time_precision,''))='day'
                 AND date_trading_proxy_utc IS NOT NULL
                  THEN 'DAY_NEXT_SESSION_PROXY_CANDIDATE'
                WHEN lower(COALESCE(time_precision,''))='day'
                  THEN 'DAY_COARSE_NO_SESSION_PROXY'
                ELSE 'UNKNOWN_PRECISION'
              END AS causal_tier
              FROM news_parsed
            )
            SELECT causal_tier,COUNT(*) AS rows FROM tiered
            GROUP BY causal_tier ORDER BY rows DESC
            """
        )
    )
    return {
        "version": VERSION,
        "status": "REVIEW_HISTORICAL_TIME_PROXY_NOT_STRICT_PIT",
        "precision_by_collection_timezone": by_precision,
        "causal_tiers": tiers,
        "strict_pit_rows": 0,
        "rules": {
            "minute_non_midnight": (
                "Historical publication-time proxy only; first_seen/retrieval replay absent."
            ),
            "minute_midnight": config["news_contract"]["minute_midnight_status"],
            "day": config["news_contract"]["day_available_at_policy"],
            "reaction_start": "OUTCOME_ONLY_NEVER_A_FEATURE",
        },
        "feature_visibility": MODEL_VISIBILITY,
        "training_authorized": False,
    }


def _news_dedup_report(connection: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    prefix_chars = int(config["news_contract"]["syndication_prefix_chars"])
    overall = _one(
        connection.execute(
            """
            SELECT COUNT(*) AS rows,
              COUNT(DISTINCT hash(lower(trim(url_raw)))) FILTER(
                WHERE url_raw IS NOT NULL AND trim(url_raw)<>'') AS distinct_url_hashes,
              COUNT(*) FILTER(WHERE url_raw IS NOT NULL AND trim(url_raw)<>'') AS rows_with_url,
              COUNT(DISTINCT hash(date,text)) AS distinct_date_text_hashes,
              COUNT(DISTINCT hash(normalized_text)) FILTER(WHERE normalized_text<>'')
                AS distinct_normalized_text_hashes
            FROM news_parsed
            """
        )
    )
    overall["url_duplicate_excess_rows"] = (
        int(overall["rows_with_url"] or 0) - int(overall["distinct_url_hashes"] or 0)
    )
    overall["date_text_duplicate_excess_rows"] = (
        int(overall["rows"] or 0) - int(overall["distinct_date_text_hashes"] or 0)
    )
    overall["normalized_text_duplicate_excess_rows"] = (
        int(overall["rows"] or 0) - int(overall["distinct_normalized_text_hashes"] or 0)
    )
    top_urls = _rows(
        connection.execute(
            """
            SELECT lower(trim(url_raw)) AS normalized_url,COUNT(*) AS rows,
                   COUNT(DISTINCT collection_source) AS collection_sources,
                   COUNT(DISTINCT normalized_url_domain) AS domains,
                   MIN(published_proxy_utc) AS min_date,MAX(published_proxy_utc) AS max_date
            FROM news_parsed WHERE url_raw IS NOT NULL AND trim(url_raw)<>''
            GROUP BY 1 HAVING COUNT(*)>1 ORDER BY rows DESC LIMIT 100
            """
        )
    )
    syndication = _one(
        connection.execute(
            f"""
            WITH candidates AS (
              SELECT CAST(published_proxy_utc AS DATE) AS publication_day,
                     hash(substr(normalized_text,1,{prefix_chars})) AS prefix_hash,
                     COUNT(*) AS rows,COUNT(DISTINCT normalized_url_domain) AS domains
              FROM news_parsed
              WHERE published_proxy_utc IS NOT NULL AND length(normalized_text)>=40
              GROUP BY 1,2 HAVING COUNT(*)>1
            )
            SELECT COUNT(*) AS repeated_prefix_day_groups,
                   SUM(rows) AS rows_in_groups,
                   SUM(domains>1) AS multi_domain_candidate_groups,
                   MAX(rows) AS largest_group_rows
            FROM candidates
            """
        )
    )
    return {
        "version": VERSION,
        "status": "PASS_DUPLICATE_AND_SYNDICATION_BASELINE_REVIEW_READY",
        "overall": overall,
        "top_repeated_urls": top_urls,
        "syndication_prefix_diagnostic": {
            "definition": (
                f"same publication UTC day plus hash of first {prefix_chars} "
                "normalized characters; candidate only, not semantic identity"
            ),
            **syndication,
        },
        "policy": config["news_contract"]["deduplication_policy"],
        "limitations": (
            "URL/text hashes quantify exact or normalization-level repetition. "
            "The prefix diagnostic nominates syndication candidates but does not "
            "perform semantic near-duplicate clustering."
        ),
        "training_authorized": False,
    }


def _asset_identity_report(connection: Any, config: Mapping[str, Any]) -> dict[str, Any]:
    connection.execute(
        """
        CREATE TEMP VIEW news_stock_links AS
        SELECT n.collection_source,n.published_proxy_utc,
               upper(trim(symbol)) AS source_ticker
        FROM news_parsed n,
        UNNEST(TRY_CAST(json_extract(n.extra_json,'$.stocks') AS VARCHAR[])) s(symbol)
        WHERE symbol IS NOT NULL AND trim(symbol)<>''
        """
    )
    bar_mapping = _one(
        connection.execute(
            """
            WITH b AS (SELECT DISTINCT upper(trim(ticker)) ticker FROM raw_bars),
                 c AS (SELECT DISTINCT upper(ticker) ticker FROM core_assets)
            SELECT (SELECT COUNT(*) FROM b) AS bar_tickers,
                   (SELECT COUNT(*) FROM c) AS core_tickers,
                   (SELECT COUNT(*) FROM b JOIN c USING(ticker)) AS exact_matches,
                   (SELECT COUNT(*) FROM b LEFT JOIN c USING(ticker)
                    WHERE c.ticker IS NULL) AS unmatched_bar_tickers,
                   (SELECT COUNT(*) FROM c LEFT JOIN b USING(ticker)
                    WHERE b.ticker IS NULL) AS core_without_bars
            """
        )
    )
    news_mapping = _one(
        connection.execute(
            """
            SELECT COUNT(*) AS source_symbol_links,
                   COUNT(DISTINCT source_ticker) AS source_tickers,
                   SUM(c.asset_id IS NOT NULL) AS exact_core_symbol_links,
                   COUNT(DISTINCT source_ticker) FILTER(WHERE c.asset_id IS NOT NULL)
                     AS exact_core_tickers,
                   COUNT(DISTINCT source_ticker) FILTER(WHERE c.asset_id IS NULL)
                     AS unresolved_source_tickers
            FROM news_stock_links n LEFT JOIN core_assets c
              ON n.source_ticker=upper(c.ticker)
            """
        )
    )
    identifier_history = _one(
        connection.execute(
            """
            SELECT COUNT(*) AS rows,COUNT(DISTINCT asset_id) AS assets,
                   SUM(valid_from IS NOT NULL) AS rows_with_valid_from,
                   SUM(valid_to IS NOT NULL) AS rows_with_valid_to
            FROM market.asset_identifier_history
            """
        )
    )
    graph = _one(
        connection.execute(
            """
            SELECT COUNT(*) AS identity_buckets,
                   COUNT(DISTINCT registrant_asset_id) AS registrant_assets,
                   COUNT(DISTINCT registrant_ticker) AS registrant_tickers,
                   SUM(identity_status='canonical') AS canonical_buckets
            FROM graph_identity.identity_evidence_buckets
            """
        )
    )
    core_missing_bars = _rows(
        connection.execute(
            """
            WITH b AS (SELECT DISTINCT upper(trim(ticker)) ticker FROM raw_bars)
            SELECT c.asset_id,c.ticker,c.sector,c.core_min_day,c.core_max_day
            FROM core_assets c LEFT JOIN b ON upper(c.ticker)=b.ticker
            WHERE b.ticker IS NULL ORDER BY c.asset_id
            """
        )
    )
    unresolved_news = _rows(
        connection.execute(
            """
            SELECT source_ticker,COUNT(*) AS links,MIN(published_proxy_utc) AS min_date,
                   MAX(published_proxy_utc) AS max_date
            FROM news_stock_links n LEFT JOIN core_assets c
              ON n.source_ticker=upper(c.ticker)
            WHERE c.asset_id IS NULL GROUP BY source_ticker
            ORDER BY links DESC LIMIT 500
            """
        )
    )
    return {
        "version": VERSION,
        "status": "REVIEW_CURRENT_SYMBOL_PROXY_NOT_HISTORICALLY_CANONICAL",
        "bars_to_core": bar_mapping,
        "news_to_core": news_mapping,
        "identifier_history": identifier_history,
        "existing_graph_identity_evidence": graph,
        "core_assets_without_bars": core_missing_bars,
        "top_unresolved_news_tickers": unresolved_news,
        "mapping_contract": config["identity_contract"],
        "interpretation": (
            "Existing graph identity evidence is reused as coverage evidence only. "
            "It cannot expand news links or retroactively prove ticker validity."
        ),
        "training_authorized": False,
    }


def _core_coverage_report(connection: Any) -> dict[str, Any]:
    per_asset = _rows(
        connection.execute(
            """
            WITH bars AS (
              SELECT ticker,COUNT(*) AS rth_asset_days,MIN(trading_day) AS bars_min_day,
                     MAX(trading_day) AS bars_max_day
              FROM intraday_daily GROUP BY ticker
            ), news AS (
              SELECT c.asset_id,COUNT(*) AS news_symbol_links,
                     COUNT(DISTINCT CAST(n.published_proxy_utc AS DATE))
                       AS news_publication_proxy_days,
                     MIN(n.published_proxy_utc) AS news_min_date,
                     MAX(n.published_proxy_utc) AS news_max_date
              FROM news_stock_links n JOIN core_assets c
                ON n.source_ticker=upper(c.ticker)
              GROUP BY c.asset_id
            )
            SELECT c.asset_id,c.ticker,c.sector,c.core_state_rows,c.core_min_day,c.core_max_day,
                   COALESCE(b.rth_asset_days,0) AS rth_asset_days,b.bars_min_day,b.bars_max_day,
                   COALESCE(n.news_symbol_links,0) AS news_symbol_links,
                   COALESCE(n.news_publication_proxy_days,0) AS news_publication_proxy_days,
                   n.news_min_date,n.news_max_date,
                   CASE WHEN b.rth_asset_days IS NOT NULL THEN 1 ELSE 0 END AS has_bars,
                   CASE WHEN n.news_symbol_links IS NOT NULL THEN 1 ELSE 0 END AS has_news
            FROM core_assets c LEFT JOIN bars b USING(ticker)
            LEFT JOIN news n USING(asset_id) ORDER BY c.asset_id
            """
        )
    )
    summary = {
        "core_assets": len(per_asset),
        "assets_with_bars": sum(int(row["has_bars"]) for row in per_asset),
        "assets_with_news": sum(int(row["has_news"]) for row in per_asset),
        "assets_with_both": sum(
            int(row["has_bars"] and row["has_news"]) for row in per_asset
        ),
        "assets_with_neither": sum(
            int(not row["has_bars"] and not row["has_news"]) for row in per_asset
        ),
        "total_exact_news_symbol_links": sum(
            int(row["news_symbol_links"]) for row in per_asset
        ),
    }
    return {
        "version": VERSION,
        "status": "PASS_CORE_INFORMATION_COVERAGE_INVENTORIED",
        "summary": summary,
        "per_asset": per_asset,
        "clock_warning": (
            "News dates are historical publication proxies, not available_at. "
            "This report is asset-level inventory and cannot be joined as a feature."
        ),
        "training_authorized": False,
    }


def run_audit(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    report_dir = resolve_path(root, config["paths"]["report_dir"])
    snapshots = {
        name: _latest_snapshot(root, config, name) for name in ("bars", "news")
    }
    before = _input_state(root, config, snapshots)
    plan = build_plan(root, config)
    report_hashes: dict[str, str] = {}
    report_hashes["plan.json"] = _atomic_json(report_dir / "plan.json", plan)
    report_dir.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix="_duckdb_spill_", dir=report_dir) as spill:
        connection = _connect(root, config, snapshots, Path(spill))
        try:
            bars_coverage = _bars_coverage_report(connection, config)
            _build_intraday_daily(connection, config)
            bars_session = _bars_session_report(connection, config)
            bars_reconciliation = _bars_reconciliation_report(connection, config)
            opening = _opening_report(connection, config)
            news_source = _news_source_report(connection, config)
            news_time = _news_time_report(connection, config)
            news_dedup = _news_dedup_report(connection, config)
            identity = _asset_identity_report(connection, config)
            core_coverage = _core_coverage_report(connection)
        finally:
            connection.close()
    reports = {
        "bars_coverage_report.json": bars_coverage,
        "bars_session_report.json": bars_session,
        "bars_daily_reconciliation_report.json": bars_reconciliation,
        "opening_semantics_report.json": opening,
        "asset_identity_report.json": identity,
        "news_source_report.json": news_source,
        "news_time_semantics_report.json": news_time,
        "news_dedup_report.json": news_dedup,
        "core_information_coverage_report.json": core_coverage,
    }
    for filename, payload in reports.items():
        report_hashes[filename] = _atomic_json(report_dir / filename, payload)
    after = _input_state(root, config, snapshots)
    unchanged = before == after
    blockers = [
        "Alpaca feed identity and redistribution provenance are unknown.",
        "Official opening auction is not separately observed in minute bars.",
        "Current-symbol ticker matches lack historical valid_from/valid_to for canonical identity.",
        "Historical news lacks first_seen/retrieval replay and remains strict_pit=false.",
        "Near-duplicate story identity requires a separate versioned clustering materializer.",
        "Publisher/domain concentration is evidence structure, not a reliability or sample-weight rule.",
    ]
    structural_failures = []
    if not unchanged:
        structural_failures.append("source_or_raw_input_mutated")
    if bars_coverage["status"] != "PASS_BARS_STRUCTURAL_COVERAGE_REVIEW_READY":
        structural_failures.append("bars_structural_anomalies")
    if int(news_source["overall"]["invalid_extra_fields_json"] or 0) != 0:
        structural_failures.append("invalid_news_extra_fields_json")
    audit = {
        "version": VERSION,
        "status": (
            "PASS_READ_ONLY_SEMANTICS_REVIEW_READY"
            if not structural_failures
            else "REVIEW_STRUCTURAL_GATE_FAILURE"
        ),
        "input_fingerprint": plan["input_fingerprint"],
        "input_state_unchanged": unchanged,
        "source_databases_mutated": not unchanged,
        "structural_failures": structural_failures,
        "scientific_blockers_before_materialization": blockers,
        "report_sha256": report_hashes,
        "report_dir": str(report_dir),
        "source_asymmetry_is_blocker": False,
        "training_authorized": False,
        "materialization_authorized": False,
        "feature_visibility": MODEL_VISIBILITY,
        "v009_interaction": "NONE",
        "interpretation": (
            "PASS authorizes review and design of a separate source-preserving "
            "point-in-time materializer only. It does not promote Alpaca, news, "
            "a graph, a feature, a model or a trading claim."
        ),
    }
    report_hashes["audit.json"] = _atomic_json(report_dir / "audit.json", audit)
    audit["audit_file_sha256"] = report_hashes["audit.json"]
    return audit


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(
        description="Read-only Public Information Semantics Audit V001"
    )
    parser.add_argument("--config", default=str(root / "config" / f"{VERSION}.json"))
    parser.add_argument("--stage", choices=["plan", "audit"], default="audit")
    args = parser.parse_args()
    config_path = Path(args.config).resolve()
    config = load_config(root, config_path)
    if args.stage == "plan":
        result = build_plan(root, config)
        report_dir = resolve_path(root, config["paths"]["report_dir"])
        result["plan_file_sha256"] = _atomic_json(report_dir / "plan.json", result)
    else:
        result = run_audit(root, config)
    print(json.dumps(_jsonable(result), indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()

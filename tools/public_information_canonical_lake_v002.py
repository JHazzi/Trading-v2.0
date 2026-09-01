from __future__ import annotations

import argparse
import hashlib
import json
import os
import shutil
import sqlite3
import tempfile
import uuid
from contextlib import closing
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Iterable, Mapping


VERSION = "public_information_canonical_lake_v002"
FEATURE_VISIBILITY = (
    "BLOCKED_UNTIL_V002_AUDIT_AND_SEPARATE_PREREGISTERED_INCREMENT_TEST"
)
MIGRATION_ID = "public_information_v002_catalog_v001"


class CanonicalLakeError(RuntimeError):
    """A storage, isolation, lineage or materialization gate failed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat().replace("+00:00", "Z")


def _jsonable(value: Any) -> Any:
    return json.loads(json.dumps(value, default=str, allow_nan=False))


def canonical_json_bytes(value: Any) -> bytes:
    return json.dumps(
        _jsonable(value), sort_keys=True, separators=(",", ":"),
        ensure_ascii=False, allow_nan=False,
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
        _jsonable(value), indent=2, sort_keys=True, ensure_ascii=False,
        allow_nan=False,
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
    connection = sqlite3.connect(f"file:{path.as_posix()}?mode=ro", uri=True)
    connection.execute("PRAGMA query_only=ON")
    return connection


def _sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _rows(cursor: Any) -> list[dict[str, Any]]:
    names = [str(item[0]) for item in cursor.description]
    return [_jsonable(dict(zip(names, row))) for row in cursor.fetchall()]


def _one(cursor: Any) -> dict[str, Any]:
    values = _rows(cursor)
    if len(values) != 1:
        raise CanonicalLakeError(f"expected one row, observed {len(values)}")
    return values[0]


def _file_state(path: Path) -> dict[str, Any]:
    if not path.exists():
        return {"path": str(path), "exists": False}
    stat = path.stat()
    return {
        "path": str(path), "exists": True, "size_bytes": stat.st_size,
        "mtime_ns": stat.st_mtime_ns,
    }


def _database_state(path: Path) -> list[dict[str, Any]]:
    return [_file_state(path)] + [
        _file_state(Path(str(path) + suffix)) for suffix in ("-wal", "-shm")
    ]


def _directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    return sum(
        item.stat().st_size for item in path.rglob("*")
        if item.is_file() and not item.is_symlink()
    )


def _tree_state(path: Path, *, hash_files: bool = True) -> dict[str, Any]:
    files = []
    if not path.exists():
        return {"exists": False, "file_count": 0, "size_bytes": 0}
    for item in sorted(path.rglob("*.parquet")):
        rel = item.relative_to(path).as_posix()
        stat = item.stat()
        files.append({
            "path": rel,
            "size_bytes": stat.st_size,
            "sha256": sha256_file(item) if hash_files else None,
        })
    basis = [{"path": x["path"], "size_bytes": x["size_bytes"],
              "sha256": x["sha256"]} for x in files]
    return {
        "exists": True,
        "file_count": len(files),
        "size_bytes": sum(int(x["size_bytes"]) for x in files),
        "tree_sha256": sha256_bytes(canonical_json_bytes(basis)),
        "files": files,
    }


def _within(path: Path, parent: Path) -> bool:
    try:
        path.resolve(strict=False).relative_to(parent.resolve(strict=False))
        return True
    except ValueError:
        return False


def validate_config(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if config.get("version") != VERSION:
        errors.append("version mismatch")
    if config.get("training_authorized") is not False:
        errors.append("training_authorized must remain false")
    if config.get("feature_visibility") != FEATURE_VISIBILITY:
        errors.append("feature_visibility mismatch")
    storage = config.get("storage", {})
    cap = int(storage.get("hard_cap_bytes", 0))
    if cap <= 0 or cap > int(storage.get("absolute_max_allowed_bytes", 0)):
        errors.append("storage cap is invalid")
    if cap > 100 * 1024 ** 3:
        errors.append("storage cap exceeds 100 GiB")
    if storage.get("preallocate_space") is not False:
        errors.append("preallocation is forbidden")
    duckdb_config = config.get("duckdb", {})
    if duckdb_config.get("preserve_insertion_order") is not False:
        errors.append("DuckDB insertion-order preservation must remain disabled")
    if duckdb_config.get("bar_materialization_batch") != "SOURCE_TICKER_GROUP":
        errors.append("bar materialization must be batched by source ticker group")
    if int(duckdb_config.get("bar_tickers_per_batch", 0)) <= 0:
        errors.append("bar tickers per batch must be positive")
    if duckdb_config.get("resume_completed_bar_batches") is not True:
        errors.append("completed bar batches must be resumable")
    bars = config.get("bars", {})
    if bars.get("cross_source_policy") != "PRESERVE_BOTH_NO_MEDIAN_NO_OVERWRITE":
        errors.append("bar median/overwrite is forbidden")
    if bars.get("volume_policy") != "NEVER_BLEND_ACROSS_FEEDS":
        errors.append("cross-feed volume blending is forbidden")
    news = config.get("news", {})
    if news.get("market_impact_t0_policy") != "OUTCOME_SIDE_CANDIDATE_ONLY_NEVER_A_FEATURE":
        errors.append("market-impact t0 must remain outcome-side only")
    if news.get("historical_strict_pit") is not False:
        errors.append("historical news cannot be strict PIT")
    paths = config.get("paths", {})
    required = {
        "intake_catalog_db", "v002_catalog_db", "v002_catalog_schema",
        "market_db", "core_db", "graph_identity_db", "lake_root", "report_root",
    }
    missing = sorted(required - set(paths))
    if missing:
        errors.append(f"missing paths: {missing}")
    operational_text = json.dumps(
        {"paths": paths, "snapshots": config.get("snapshots", {})},
        sort_keys=True,
    ).lower()
    for token in config.get("guards", {}).get("forbidden_path_tokens", []):
        if token.lower() in operational_text:
            errors.append(f"forbidden V009 token configured: {token}")
    lake = resolve_path(root, paths.get("lake_root", "invalid"))
    reports = resolve_path(root, paths.get("report_root", "invalid"))
    catalog = resolve_path(root, paths.get("v002_catalog_db", "invalid"))
    for protected in config.get("guards", {}).get("protected_write_paths", []):
        target = resolve_path(root, protected)
        if any(_within(candidate, target) or _within(target, candidate)
               for candidate in (lake, reports, catalog)):
            errors.append(f"output intersects protected path: {target}")
    if not all(_within(candidate, root) for candidate in (lake, reports, catalog)):
        errors.append("output root validation failed")
    return {
        "valid": not errors,
        "errors": errors,
        "training_authorized": False,
        "feature_visibility": FEATURE_VISIBILITY,
    }


def load_config(root: Path, path: Path) -> dict[str, Any]:
    config = read_json(path)
    validation = validate_config(root, config)
    if not validation["valid"]:
        raise CanonicalLakeError("invalid config: " + "; ".join(validation["errors"]))
    return config


def _latest_snapshot(root: Path, config: Mapping[str, Any], name: str) -> dict[str, Any]:
    spec = config["snapshots"][name]
    catalog = resolve_path(root, config["paths"]["intake_catalog_db"])
    with closing(_readonly_sqlite(catalog)) as connection:
        row = connection.execute(
            """
            SELECT snapshot_id,resolved_revision,manifest_sha256,manifest_path,
                   selected_file_count,selected_bytes
            FROM dataset_snapshots
            WHERE dataset_key=? AND profile_name=?
            ORDER BY last_verified_at_utc DESC,snapshot_id DESC LIMIT 1
            """,
            (spec["dataset_key"], spec["profile_name"]),
        ).fetchone()
        if row is None:
            raise CanonicalLakeError(f"missing snapshot for {name}")
        file_rows = connection.execute(
            """
            SELECT repo_path,size_bytes,local_path,status,local_size_bytes
            FROM snapshot_files WHERE snapshot_id=? ORDER BY repo_path
            """, (row[0],),
        ).fetchall()
    files = []
    for repo_path, size_bytes, local_path, status, local_size in file_rows:
        path = Path(local_path)
        observed = path.stat().st_size if path.exists() else None
        complete = path.exists() and observed == int(size_bytes) and status == "COMPLETE"
        files.append({
            "repo_path": repo_path, "size_bytes": int(size_bytes),
            "local_path": str(path), "catalog_status": status,
            "catalog_local_size_bytes": local_size,
            "observed_size_bytes": observed, "complete": complete,
        })
    if not files or not all(item["complete"] for item in files):
        raise CanonicalLakeError(f"snapshot {row[0]} is incomplete")
    manifest = read_json(Path(row[3]))
    if manifest.get("manifest_sha256") != row[2]:
        raise CanonicalLakeError(f"manifest/catalog mismatch for {row[0]}")
    return {
        "name": name, "dataset_key": spec["dataset_key"],
        "profile_name": spec["profile_name"], "snapshot_id": row[0],
        "resolved_revision": row[1], "manifest_sha256": row[2],
        "manifest_path": row[3], "selected_file_count": int(row[4]),
        "selected_bytes": int(row[5]), "files": files,
    }


def _input_state(root: Path, config: Mapping[str, Any], snapshots: Mapping[str, Any]) -> dict[str, Any]:
    return {
        "databases": {
            key: _database_state(resolve_path(root, config["paths"][key]))
            for key in ("intake_catalog_db", "market_db", "core_db", "graph_identity_db")
        },
        "parquet": {
            name: [_file_state(Path(item["local_path"])) for item in snap["files"]]
            for name, snap in snapshots.items()
        },
    }


def _parquet_scan(paths: Iterable[str]) -> str:
    values = ",".join(_sql_string(path) for path in paths)
    if not values:
        raise CanonicalLakeError("empty Parquet source")
    return f"read_parquet([{values}], union_by_name=true, filename=true)"


def _artifact_scan(path: Path) -> str:
    return f"read_parquet({_sql_string(path / '**' / '*.parquet')}, hive_partitioning=true)"


def _managed_size(root: Path, config: Mapping[str, Any]) -> int:
    return sum(
        _directory_size(resolve_path(root, value))
        for value in config["storage"]["managed_roots"]
    )


def storage_gate(root: Path, config: Mapping[str, Any], stage: str) -> dict[str, Any]:
    managed = _managed_size(root, config)
    estimate = int(config["storage"]["stage_estimate_bytes"].get(stage, 0))
    lake_root = resolve_path(root, config["paths"]["lake_root"])
    lake_root.mkdir(parents=True, exist_ok=True)
    usage = shutil.disk_usage(lake_root)
    cap = int(config["storage"]["hard_cap_bytes"])
    minimum_free = int(config["storage"]["minimum_free_after_operation_bytes"])
    return {
        "stage": stage,
        "managed_bytes": managed,
        "estimated_new_bytes": estimate,
        "projected_managed_bytes": managed + estimate,
        "hard_cap_bytes": cap,
        "filesystem_free_bytes": usage.free,
        "minimum_free_after_operation_bytes": minimum_free,
        "preallocation_performed": False,
        "pass": managed + estimate <= cap and usage.free - estimate >= minimum_free,
    }


def initialize_catalog(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    path = resolve_path(root, config["paths"]["v002_catalog_db"])
    schema_path = resolve_path(root, config["paths"]["v002_catalog_schema"])
    schema = schema_path.read_text(encoding="utf-8")
    schema_sha = sha256_bytes(schema.encode("utf-8"))
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(schema)
        connection.execute(
            """
            INSERT OR IGNORE INTO schema_migrations
            (migration_id,applied_at_utc,schema_sha256) VALUES(?,?,?)
            """, (MIGRATION_ID, utc_now(), schema_sha),
        )
    return {"path": str(path), "schema_sha256": schema_sha}


def build_plan(root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    snapshots = {name: _latest_snapshot(root, config, name) for name in ("bars", "news")}
    inputs = _input_state(root, config, snapshots)
    basis = {
        "version": VERSION,
        "config_sha256": sha256_bytes(canonical_json_bytes(config)),
        "snapshots": {
            name: {key: snap[key] for key in (
                "snapshot_id", "resolved_revision", "manifest_sha256",
                "selected_file_count", "selected_bytes",
            )} for name, snap in snapshots.items()
        },
        "input_state": inputs,
    }
    fingerprint = sha256_bytes(canonical_json_bytes(basis))
    build_id = f"build_{fingerprint[:24]}"
    lake_path = resolve_path(root, config["paths"]["lake_root"]) / build_id
    report_path = resolve_path(root, config["paths"]["report_root"]) / build_id
    return {
        "version": VERSION,
        "status": "READY_FOR_CANONICAL_MATERIALIZATION",
        "build_id": build_id,
        "input_fingerprint": fingerprint,
        "config_validation": validate_config(root, config),
        "inputs": basis,
        "snapshots_full": snapshots,
        "lake_path": str(lake_path),
        "report_path": str(report_path),
        "storage_gates": {
            stage: storage_gate(root, config, stage) for stage in ("bars", "news")
        },
        "clock_contract": {
            "publication_is_not_impact_t0": True,
            "market_impact_t0": "OUTCOME_SIDE_ONLY_NOT_MATERIALIZED_AS_FEATURE",
            "historical_news_strict_pit": False,
            "bar_first_seen_role": "LINEAGE_ONLY",
            "bar_availability": "SEGMENT_END_PROXY",
        },
        "training_authorized": False,
        "feature_visibility": FEATURE_VISIBILITY,
        "v009_interaction": "NONE",
    }


def _register_build(root: Path, config: Mapping[str, Any], plan: Mapping[str, Any]) -> None:
    initialize_catalog(root, config)
    path = resolve_path(root, config["paths"]["v002_catalog_db"])
    now = utc_now()
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO canonical_builds
            (build_id,contract_version,input_fingerprint,config_sha256,
             bars_snapshot_id,news_snapshot_id,lake_path,report_path,status,
             training_authorized,created_at_utc,updated_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,0,?,?)
            ON CONFLICT(build_id) DO UPDATE SET updated_at_utc=excluded.updated_at_utc
            """,
            (plan["build_id"], VERSION, plan["input_fingerprint"],
             plan["inputs"]["config_sha256"],
             plan["inputs"]["snapshots"]["bars"]["snapshot_id"],
             plan["inputs"]["snapshots"]["news"]["snapshot_id"],
             plan["lake_path"], plan["report_path"], "PLANNED", now, now),
        )


def _stage_start(root: Path, config: Mapping[str, Any], plan: Mapping[str, Any], stage: str) -> str:
    run_id = f"run_{uuid.uuid4().hex}"
    path = resolve_path(root, config["paths"]["v002_catalog_db"])
    with sqlite3.connect(path) as connection:
        connection.execute(
            "INSERT INTO stage_runs(run_id,build_id,stage,status,started_at_utc) VALUES(?,?,?,?,?)",
            (run_id, plan["build_id"], stage, "RUNNING", utc_now()),
        )
    return run_id


def _stage_finish(root: Path, config: Mapping[str, Any], plan: Mapping[str, Any], run_id: str,
                  stage: str, status: str, marker: Path | None = None,
                  error: Mapping[str, Any] | None = None) -> None:
    path = resolve_path(root, config["paths"]["v002_catalog_db"])
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            UPDATE stage_runs SET status=?,finished_at_utc=?,marker_path=?,error_json=?
            WHERE run_id=?
            """,
            (status, utc_now(), str(marker) if marker else None,
             json.dumps(_jsonable(error), sort_keys=True) if error else None, run_id),
        )
        build_status = (
            "AUDITED" if stage == "audit" and status == "COMPLETED"
            else f"{stage.upper()}_COMPLETE" if status == "COMPLETED"
            else "FAILED_REVIEW"
        )
        connection.execute(
            "UPDATE canonical_builds SET status=?,updated_at_utc=? WHERE build_id=?",
            (build_status, utc_now(), plan["build_id"]),
        )


def _record_artifact(root: Path, config: Mapping[str, Any], plan: Mapping[str, Any],
                     stage: str, name: str, state: Mapping[str, Any], row_count: int) -> None:
    path = resolve_path(root, config["paths"]["v002_catalog_db"])
    artifact_path = Path(plan["lake_path"]) / name
    with sqlite3.connect(path) as connection:
        connection.execute(
            """
            INSERT INTO build_artifacts
            (build_id,stage,artifact_name,artifact_path,file_count,size_bytes,
             tree_sha256,row_count,status,created_at_utc)
            VALUES(?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(build_id,artifact_name) DO UPDATE SET
              file_count=excluded.file_count,size_bytes=excluded.size_bytes,
              tree_sha256=excluded.tree_sha256,row_count=excluded.row_count,
              status=excluded.status
            """,
            (plan["build_id"], stage, name, str(artifact_path),
             int(state["file_count"]), int(state["size_bytes"]), state["tree_sha256"],
             int(row_count), "COMPLETE", utc_now()),
        )


def _connect(root: Path, config: Mapping[str, Any], plan: Mapping[str, Any], spill: Path):
    try:
        import duckdb
    except ImportError as exc:
        raise CanonicalLakeError("DuckDB is required") from exc
    connection = duckdb.connect(":memory:")
    connection.execute(f"SET threads={int(config['duckdb']['threads'])}")
    connection.execute(f"SET memory_limit={_sql_string(config['duckdb']['memory_limit'])}")
    connection.execute(f"SET temp_directory={_sql_string(spill)}")
    preserve = str(bool(config["duckdb"]["preserve_insertion_order"])).lower()
    connection.execute(f"SET preserve_insertion_order={preserve}")
    for alias, key in (("market", "market_db"), ("core", "core_db"),
                       ("graph_identity", "graph_identity_db")):
        path = resolve_path(root, config["paths"][key])
        connection.execute(f"ATTACH {_sql_string(path)} AS {alias} (TYPE SQLITE, READ_ONLY)")
    bars_scan = _parquet_scan(
        item["local_path"] for item in plan["snapshots_full"]["bars"]["files"]
    )
    news_scan = _parquet_scan(
        item["local_path"] for item in plan["snapshots_full"]["news"]["files"]
    )
    connection.execute(f"CREATE TEMP VIEW raw_bars AS SELECT * FROM {bars_scan}")
    connection.execute(f"CREATE TEMP VIEW raw_news AS SELECT * FROM {news_scan}")
    connection.execute(
        """
        CREATE TEMP VIEW core_assets AS
        SELECT asset_id,upper(ticker) AS ticker,ANY_VALUE(sector) AS sector,
               MIN(trading_day) AS min_day,MAX(trading_day) AS max_day
        FROM core.market_daily_v003_states GROUP BY asset_id,upper(ticker)
        """
    )
    connection.execute(
        """
        CREATE TEMP VIEW core_sessions AS
        SELECT DISTINCT CAST(trading_day AS DATE) AS trading_day
        FROM core.market_daily_v003_states
        """
    )
    return connection


def _copy_dataset(connection: Any, query: str, destination: Path,
                  partition_columns: list[str], config: Mapping[str, Any],
                  build_id: str, artifact_name: str) -> tuple[dict[str, Any], int, bool]:
    success_path = destination / "_SUCCESS.json"
    if success_path.exists():
        marker = read_json(success_path)
        if marker.get("build_id") != build_id or marker.get("artifact_name") != artifact_name:
            raise CanonicalLakeError(f"artifact marker mismatch: {destination}")
        state = _tree_state(destination, hash_files=True)
        expected = marker["tree_state"]
        if (state["tree_sha256"], state["file_count"], state["size_bytes"]) != (
            expected["tree_sha256"], expected["file_count"], expected["size_bytes"]
        ):
            raise CanonicalLakeError(f"artifact changed after publication: {destination}")
        return state, int(marker["row_count"]), True
    if destination.exists():
        raise CanonicalLakeError(f"unsealed artifact directory requires review: {destination}")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.TemporaryDirectory(prefix=f"_{artifact_name}_", dir=destination.parent) as td:
        temporary = Path(td) / artifact_name
        compression = config["duckdb"]["compression"]
        row_group = int(config["duckdb"]["row_group_size"])
        partition = (
            ", PARTITION_BY (" + ",".join(partition_columns) + ")"
            if partition_columns else ""
        )
        connection.execute(
            f"COPY ({query}) TO {_sql_string(temporary)} "
            f"(FORMAT PARQUET, COMPRESSION {compression}, ROW_GROUP_SIZE {row_group}{partition})"
        )
        scan = _artifact_scan(temporary)
        row_count = int(_one(connection.execute(f"SELECT COUNT(*) AS n FROM {scan}"))["n"])
        state = _tree_state(temporary, hash_files=True)
        if not state["file_count"] or row_count <= 0:
            raise CanonicalLakeError(f"artifact {artifact_name} is empty")
        _atomic_json(temporary / "_SUCCESS.json", {
            "version": VERSION, "build_id": build_id,
            "artifact_name": artifact_name, "row_count": row_count,
            "tree_state": state, "created_at_utc": utc_now(),
            "training_authorized": False,
        })
        os.replace(temporary, destination)
    return state, row_count, False


def _copy_dataset_by_query_batches(
    connection: Any,
    batches: list[tuple[str, str]],
    destination: Path,
    partition_columns: list[str],
    config: Mapping[str, Any],
    build_id: str,
    artifact_name: str,
    progress_status: str,
    batch_strategy: str,
) -> tuple[dict[str, Any], int, bool]:
    """Materialize bounded, resumable query batches with partitions.

    Each batch is sealed independently below a build-specific work directory.
    A retry reuses completed batches, while the public artifact is published
    atomically only after every batch has completed.
    """
    success_path = destination / "_SUCCESS.json"
    if success_path.exists():
        marker = read_json(success_path)
        if marker.get("build_id") != build_id or marker.get("artifact_name") != artifact_name:
            raise CanonicalLakeError(f"artifact marker mismatch: {destination}")
        state = _tree_state(destination, hash_files=True)
        expected = marker["tree_state"]
        if (state["tree_sha256"], state["file_count"], state["size_bytes"]) != (
            expected["tree_sha256"], expected["file_count"], expected["size_bytes"]
        ):
            raise CanonicalLakeError(f"artifact changed after publication: {destination}")
        return state, int(marker["row_count"]), True
    if destination.exists():
        raise CanonicalLakeError(f"unsealed artifact directory requires review: {destination}")

    labels = [label for label, _ in batches]
    work = destination.parent / f".{artifact_name}.{build_id}.incomplete"
    work_marker = work / "_BATCH_STATE.json"
    if work.exists():
        if not work_marker.exists():
            raise CanonicalLakeError(f"unsealed batch work directory requires review: {work}")
        prior = read_json(work_marker)
        if (
            prior.get("build_id") != build_id
            or prior.get("artifact_name") != artifact_name
            or prior.get("batch_labels") != labels
        ):
            raise CanonicalLakeError(f"batch work marker mismatch: {work}")
    else:
        work.mkdir(parents=True, exist_ok=False)
        _atomic_json(work_marker, {
            "version": VERSION,
            "build_id": build_id,
            "artifact_name": artifact_name,
            "batch_labels": labels,
            "created_at_utc": utc_now(),
            "training_authorized": False,
        })

    compression = config["duckdb"]["compression"]
    row_group = int(config["duckdb"]["row_group_size"])
    partition = (
        ", PARTITION_BY (" + ",".join(partition_columns) + ")"
        if partition_columns else ""
    )
    total_rows = 0
    batches_root = work / "_batches"
    batches_root.mkdir(parents=True, exist_ok=True)
    for index, (label, query) in enumerate(batches, start=1):
        batch = batches_root / label
        batch_marker = batch / "_SUCCESS.json"
        reused = False
        if batch_marker.exists():
            marker = read_json(batch_marker)
            state = _tree_state(batch, hash_files=True)
            expected = marker["tree_state"]
            if (
                marker.get("build_id") != build_id
                or marker.get("artifact_name") != artifact_name
                or marker.get("batch_label") != label
                or (state["tree_sha256"], state["file_count"], state["size_bytes"])
                != (expected["tree_sha256"], expected["file_count"], expected["size_bytes"])
            ):
                raise CanonicalLakeError(f"bar batch marker mismatch: {batch}")
            count = int(marker["row_count"])
            reused = True
        else:
            if batch.exists():
                raise CanonicalLakeError(f"unsealed bar batch requires review: {batch}")
            with tempfile.TemporaryDirectory(prefix=f"_{label}_", dir=work) as td:
                temporary = Path(td) / label
                connection.execute(
                    f"COPY ({query}) TO {_sql_string(temporary)} "
                    f"(FORMAT PARQUET, COMPRESSION {compression}, "
                    f"ROW_GROUP_SIZE {row_group}{partition})"
                )
                state = _tree_state(temporary, hash_files=True)
                count = 0
                if state["file_count"]:
                    scan = _artifact_scan(temporary)
                    count = int(_one(connection.execute(
                        f"SELECT COUNT(*) AS n FROM {scan}"
                    ))["n"])
                else:
                    temporary.mkdir(parents=True, exist_ok=True)
                _atomic_json(temporary / "_SUCCESS.json", {
                    "version": VERSION,
                    "build_id": build_id,
                    "artifact_name": artifact_name,
                    "batch_label": label,
                    "row_count": count,
                    "tree_state": state,
                    "created_at_utc": utc_now(),
                    "training_authorized": False,
                })
                os.replace(temporary, batch)
        total_rows += count
        print(json.dumps({
            "status": progress_status,
            "artifact_name": artifact_name,
            "batch_label": label,
            "rows": count,
            "batch": index,
            "batches": len(batches),
            "reused": reused,
        }), flush=True)

    state = _tree_state(work, hash_files=True)
    if not state["file_count"] or total_rows <= 0:
        raise CanonicalLakeError(f"artifact {artifact_name} is empty")
    _atomic_json(work / "_SUCCESS.json", {
        "version": VERSION,
        "build_id": build_id,
        "artifact_name": artifact_name,
        "row_count": total_rows,
        "tree_state": state,
        "batch_strategy": batch_strategy,
        "batch_labels": labels,
        "created_at_utc": utc_now(),
        "training_authorized": False,
    })
    os.replace(work, destination)
    return state, total_rows, False


def _write_stage_marker(path: Path, plan: Mapping[str, Any], stage: str,
                        artifacts: Mapping[str, Any], input_unchanged: bool) -> dict[str, Any]:
    marker = {
        "version": VERSION, "build_id": plan["build_id"], "stage": stage,
        "status": "PASS_STAGE_MATERIALIZED" if input_unchanged else "FAIL_INPUT_CHANGED",
        "input_fingerprint": plan["input_fingerprint"],
        "input_state_unchanged": input_unchanged,
        "artifacts": artifacts, "training_authorized": False,
        "feature_visibility": FEATURE_VISIBILITY, "v009_interaction": "NONE",
        "finished_at_utc": utc_now(),
    }
    marker["marker_sha256"] = sha256_bytes(canonical_json_bytes(marker))
    _atomic_json(path, marker)
    return marker


def _bar_sessions_query(
    config: Mapping[str, Any],
    raw_tickers: list[str] | None = None,
) -> str:
    zone = config["bars"]["exchange_timezone"]
    pre_start, pre_end = config["bars"]["session_windows_local"]["premarket"]
    rth_start, rth_end = config["bars"]["session_windows_local"]["rth"]
    aft_start, aft_end = config["bars"]["session_windows_local"]["afterhours"]
    expected = int(config["bars"]["expected_full_rth_minutes"])
    source_id = config["bars"]["source_id"]
    source_filter = ""
    if raw_tickers is not None:
        if not raw_tickers:
            raise CanonicalLakeError("bar ticker batch cannot be empty")
        source_filter = "WHERE b.ticker IN (" + ",".join(
            _sql_string(ticker) for ticker in raw_tickers
        ) + ")"
    return f"""
    WITH localized AS (
      SELECT upper(trim(b.ticker)) AS ticker,b.timestamp,b.open,b.high,b.low,b.close,
             b.volume,b.trade_count,b.vol_weighted_avg_price,
             timezone({_sql_string(zone)},b.timestamp) AS local_ts
      FROM raw_bars b
      {source_filter}
    ), classified AS (
      SELECT *,CAST(local_ts AS DATE) AS trading_day,CAST(local_ts AS TIME) AS local_time,
        CASE
          WHEN EXTRACT(isodow FROM local_ts) IN (6,7) THEN 'WEEKEND'
          WHEN CAST(local_ts AS TIME)>=TIME {_sql_string(pre_start)}
           AND CAST(local_ts AS TIME)<TIME {_sql_string(pre_end)} THEN 'PREMARKET'
          WHEN CAST(local_ts AS TIME)>=TIME {_sql_string(rth_start)}
           AND CAST(local_ts AS TIME)<TIME {_sql_string(rth_end)} THEN 'RTH'
          WHEN CAST(local_ts AS TIME)>=TIME {_sql_string(aft_start)}
           AND CAST(local_ts AS TIME)<TIME {_sql_string(aft_end)} THEN 'AFTERHOURS'
          ELSE 'OUTSIDE_STANDARD_EXTENDED' END AS session_class
      FROM localized
    ), aggregated AS (
      SELECT ticker,trading_day,
        COUNT(*) AS source_rows,COUNT(DISTINCT timestamp) AS distinct_minutes,
        MIN(timestamp) AS first_bar_utc,MAX(timestamp) AS last_bar_utc,
        COUNT(*) FILTER(WHERE session_class='PREMARKET') AS premarket_rows,
        arg_min(open,timestamp) FILTER(WHERE session_class='PREMARKET') AS premarket_open,
        MAX(high) FILTER(WHERE session_class='PREMARKET') AS premarket_high,
        MIN(low) FILTER(WHERE session_class='PREMARKET') AS premarket_low,
        arg_max(close,timestamp) FILTER(WHERE session_class='PREMARKET') AS premarket_close,
        SUM(volume) FILTER(WHERE session_class='PREMARKET') AS premarket_volume,
        COUNT(*) FILTER(WHERE session_class='RTH') AS rth_rows,
        arg_min(open,timestamp) FILTER(WHERE session_class='RTH') AS rth_open,
        arg_min(close,timestamp) FILTER(WHERE session_class='RTH') AS first_minute_close,
        MAX(high) FILTER(WHERE session_class='RTH') AS rth_high,
        MIN(low) FILTER(WHERE session_class='RTH') AS rth_low,
        arg_max(close,timestamp) FILTER(WHERE session_class='RTH') AS rth_close,
        SUM(volume) FILTER(WHERE session_class='RTH') AS rth_volume,
        SUM(trade_count) FILTER(WHERE session_class='RTH') AS rth_trade_count,
        SUM(vol_weighted_avg_price*volume) FILTER(
          WHERE session_class='RTH' AND local_time<TIME '09:35:00')
          /NULLIF(SUM(volume) FILTER(
          WHERE session_class='RTH' AND local_time<TIME '09:35:00'),0)
          AS first_five_minute_vwap,
        SUM(vol_weighted_avg_price*volume) FILTER(
          WHERE session_class='RTH' AND local_time<TIME '10:00:00')
          /NULLIF(SUM(volume) FILTER(
          WHERE session_class='RTH' AND local_time<TIME '10:00:00'),0)
          AS first_thirty_minute_vwap,
        COUNT(*) FILTER(WHERE session_class='AFTERHOURS') AS afterhours_rows,
        arg_min(open,timestamp) FILTER(WHERE session_class='AFTERHOURS') AS afterhours_open,
        MAX(high) FILTER(WHERE session_class='AFTERHOURS') AS afterhours_high,
        MIN(low) FILTER(WHERE session_class='AFTERHOURS') AS afterhours_low,
        arg_max(close,timestamp) FILTER(WHERE session_class='AFTERHOURS') AS afterhours_close,
        SUM(volume) FILTER(WHERE session_class='AFTERHOURS') AS afterhours_volume,
        COUNT(*) FILTER(WHERE session_class='OUTSIDE_STANDARD_EXTENDED') AS outside_rows,
        COUNT(*) FILTER(WHERE session_class='WEEKEND') AS weekend_rows
      FROM classified GROUP BY ticker,trading_day
    )
    SELECT EXTRACT(year FROM a.trading_day)::INTEGER AS trading_year,
      a.*,c.asset_id,c.sector,
      CASE WHEN c.asset_id IS NULL THEN 'UNRESOLVED_CURRENT_SYMBOL'
           ELSE 'EXACT_CURRENT_SYMBOL_PROXY_NOT_HISTORICALLY_CANONICAL' END
        AS identity_status,
      CASE WHEN rth_rows={expected} THEN 'FULL_390_PROXY'
           WHEN rth_rows=0 THEN 'NO_RTH'
           WHEN rth_rows<{expected} THEN 'PARTIAL_OR_SHORT_SESSION_REVIEW'
           ELSE 'ABOVE_EXPECTED_REVIEW' END AS rth_completeness_status,
      rth_rows/{float(expected)} AS rth_completeness_ratio,
      timezone({_sql_string(zone)},CAST(a.trading_day AS TIMESTAMP)+INTERVAL '9 hours 30 minutes')
        AS premarket_available_at_proxy_utc,
      timezone({_sql_string(zone)},CAST(a.trading_day AS TIMESTAMP)+INTERVAL '16 hours')
        AS rth_available_at_proxy_utc,
      timezone({_sql_string(zone)},CAST(a.trading_day AS TIMESTAMP)+INTERVAL '20 hours')
        AS afterhours_available_at_proxy_utc,
      {_sql_string(source_id)} AS source_id,
      {_sql_string(config['bars']['timestamp_semantics'])} AS timestamp_semantics,
      false AS historical_strict_pit,false AS first_seen_observed,
      'SEGMENT_END_PROXY_NEVER_A_FIRST_SEEN_CLAIM' AS availability_status
    FROM aggregated a LEFT JOIN core_assets c USING(ticker)
    """


def _bar_session_ticker_queries(
    connection: Any, config: Mapping[str, Any]
) -> list[tuple[str, str]]:
    rows = connection.execute(
        """
        SELECT ticker,upper(trim(ticker)) AS canonical_ticker
        FROM (SELECT DISTINCT ticker FROM raw_bars WHERE ticker IS NOT NULL)
        WHERE trim(ticker)<>'' ORDER BY canonical_ticker,ticker
        """
    ).fetchall()
    if not rows:
        raise CanonicalLakeError("raw bars are empty")
    by_canonical: dict[str, list[str]] = {}
    for raw_ticker, canonical_ticker in rows:
        by_canonical.setdefault(str(canonical_ticker), []).append(str(raw_ticker))
    canonical = sorted(by_canonical)
    size = int(config["duckdb"]["bar_tickers_per_batch"])
    batches: list[tuple[str, str]] = []
    for index, offset in enumerate(range(0, len(canonical), size), start=1):
        names = canonical[offset:offset + size]
        raw = [ticker for name in names for ticker in by_canonical[name]]
        digest = sha256_bytes(canonical_json_bytes(names))[:10]
        label = f"batch_{index:03d}_{digest}"
        batches.append((label, _bar_sessions_query(config, raw)))
    return batches


def _reconciliation_query(config: Mapping[str, Any], bars_path: Path) -> str:
    bars = _artifact_scan(bars_path)
    level_tolerance = float(config["bars"]["level_match_tolerance_pct"])
    return_tolerance = float(config["bars"]["return_match_tolerance_pct_points"])
    return f"""
    WITH b AS (SELECT * FROM {bars} WHERE asset_id IS NOT NULL AND rth_close>0),
    yahoo_ranked AS (
      SELECT o.asset_id,CAST(o.trading_day AS DATE) AS trading_day,
             v.open AS yahoo_open,v.high AS yahoo_high,v.low AS yahoo_low,
             v.close AS yahoo_close,v.volume AS yahoo_volume,
             ROW_NUMBER() OVER(PARTITION BY o.asset_id,o.trading_day
               ORDER BY o.observation_sequence DESC,o.observed_at DESC,
                        o.price_observation_id DESC) AS row_rank
      FROM market.price_bar_observations o JOIN market.price_bar_versions v
        ON v.price_bar_version_id=o.price_bar_version_id
      WHERE o.source_id='yahoo_finance'
    ), actions AS (
      SELECT asset_id,CAST(effective_trading_day AS DATE) AS trading_day,
             COUNT(*) AS action_versions
      FROM market.corporate_action_versions WHERE is_present=1
      GROUP BY asset_id,CAST(effective_trading_day AS DATE)
    ), joined AS (
      SELECT b.*,y.yahoo_open,y.yahoo_high,y.yahoo_low,y.yahoo_close,y.yahoo_volume,
             COALESCE(a.action_versions,0) AS action_versions,
             100.0*(b.rth_open/y.yahoo_open-1.0) AS open_level_diff_pct,
             100.0*(b.rth_high/y.yahoo_high-1.0) AS high_level_diff_pct,
             100.0*(b.rth_low/y.yahoo_low-1.0) AS low_level_diff_pct,
             100.0*(b.rth_close/y.yahoo_close-1.0) AS close_level_diff_pct,
             LAG(b.rth_close) OVER(PARTITION BY b.asset_id ORDER BY b.trading_day)
               AS previous_intraday_close,
             LAG(y.yahoo_close) OVER(PARTITION BY b.asset_id ORDER BY b.trading_day)
               AS previous_yahoo_close
      FROM b JOIN yahoo_ranked y ON y.asset_id=b.asset_id
        AND y.trading_day=b.trading_day AND y.row_rank=1
      LEFT JOIN actions a ON a.asset_id=b.asset_id AND a.trading_day=b.trading_day
    ), scored AS (
      SELECT *,
        CASE WHEN previous_intraday_close>0 AND previous_yahoo_close>0 THEN
          100.0*(rth_close/previous_intraday_close-1.0)
          -100.0*(yahoo_close/previous_yahoo_close-1.0) END
          AS close_return_diff_pct_points,
        GREATEST(ABS(open_level_diff_pct),ABS(high_level_diff_pct),
                 ABS(low_level_diff_pct),ABS(close_level_diff_pct)) AS max_level_diff_pct
      FROM joined
    )
    SELECT EXTRACT(year FROM trading_day)::INTEGER AS trading_year,*,
      CASE
        WHEN action_versions>0 THEN 'CORPORATE_ACTION_DAY_REVIEW'
        WHEN max_level_diff_pct<={level_tolerance} THEN 'NEAR_LEVEL_MATCH'
        WHEN ABS(close_return_diff_pct_points)<={return_tolerance}
          THEN 'LIKELY_ADJUSTMENT_REGIME'
        ELSE 'UNEXPLAINED_CROSS_SOURCE_DIFFERENCE' END AS reconciliation_status,
      'PRESERVE_BOTH_NO_MEDIAN_NO_OVERWRITE' AS cross_source_policy,
      'NEVER_BLEND_ACROSS_FEEDS' AS volume_policy
    FROM scored
    """


def materialize_bars(root: Path, config: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    gate = storage_gate(root, config, "bars")
    if not gate["pass"]:
        raise CanonicalLakeError(f"bars storage gate failed: {gate}")
    report_dir = Path(plan["report_path"])
    marker_path = report_dir / "bars_stage.json"
    if marker_path.exists():
        marker = read_json(marker_path)
        if marker.get("build_id") == plan["build_id"] and marker.get("status") == "PASS_STAGE_MATERIALIZED":
            return {**marker, "idempotent_reuse": True}
    run_id = _stage_start(root, config, plan, "bars")
    before = _input_state(root, config, plan["snapshots_full"])
    artifacts: dict[str, Any] = {}
    lake = Path(plan["lake_path"])
    lake.mkdir(parents=True, exist_ok=True)
    try:
        with tempfile.TemporaryDirectory(prefix="_duckdb_bars_", dir=lake) as spill:
            connection = _connect(root, config, plan, Path(spill))
            try:
                name = "bar_sessions"
                state, count, reused = _copy_dataset_by_query_batches(
                    connection, _bar_session_ticker_queries(connection, config),
                    lake / name, ["trading_year"], config, plan["build_id"], name,
                    "BAR_TICKER_BATCH_COMPLETE",
                    "SOURCE_TICKER_GROUP_WITH_LOCAL_YEAR_PARTITIONS",
                )
                artifacts[name] = {"tree_state": state, "row_count": count, "reused": reused}
                _record_artifact(root, config, plan, "bars", name, state, count)
                name = "bar_source_reconciliation"
                state, count, reused = _copy_dataset(
                    connection, _reconciliation_query(config, lake / "bar_sessions"),
                    lake / name, ["trading_year"], config, plan["build_id"], name,
                )
                artifacts[name] = {"tree_state": state, "row_count": count, "reused": reused}
                _record_artifact(root, config, plan, "bars", name, state, count)
            finally:
                connection.close()
        after = _input_state(root, config, plan["snapshots_full"])
        marker = _write_stage_marker(marker_path, plan, "bars", artifacts, before == after)
        if marker["status"] != "PASS_STAGE_MATERIALIZED":
            raise CanonicalLakeError("source inputs changed during bars materialization")
        _stage_finish(root, config, plan, run_id, "bars", "COMPLETED", marker_path)
        return marker
    except Exception as exc:
        _stage_finish(root, config, plan, run_id, "bars", "FAILED", error={"error": str(exc)})
        raise


def _create_news_parsed(connection: Any, config: Mapping[str, Any]) -> None:
    connection.execute(
        """
        CREATE TEMP TABLE calendar_map AS
        WITH bounds AS (SELECT MIN(trading_day) lo,MAX(trading_day) hi FROM core_sessions),
        days AS (SELECT CAST(x AS DATE) AS calendar_day FROM bounds,
          generate_series(lo,hi,INTERVAL 1 DAY) t(x))
        SELECT d.calendar_day,
          MAX(c.trading_day) FILTER(WHERE c.trading_day=d.calendar_day) AS same_session,
          MIN(n.trading_day) AS next_session
        FROM days d LEFT JOIN core_sessions c ON c.trading_day=d.calendar_day
        LEFT JOIN core_sessions n ON n.trading_day>d.calendar_day
        GROUP BY d.calendar_day
        """
    )
    connection.execute(
        """
        CREATE TEMP VIEW news_parsed AS
        WITH typed AS (
          SELECT date AS raw_date,text,filename,TRY_CAST(extra_fields AS JSON) AS j,
                 TRY_CAST(date AS TIMESTAMP) AS source_clock_ts
          FROM raw_news
        ), fields AS (
          SELECT *,json_extract_string(j,'$.dataset') AS collection_source,
            json_extract_string(j,'$.dataset_source') AS dataset_source,
            json_extract_string(j,'$.publisher') AS publisher_raw,
            json_extract_string(j,'$.source') AS source_raw,
            json_extract_string(j,'$.url') AS url_raw,
            json_extract_string(j,'$.time_precision') AS time_precision,
            json_extract_string(j,'$.tz_hint') AS tz_hint,
            json_extract(j,'$.stocks') AS stocks_json,
            regexp_replace(lower(trim(COALESCE(json_extract_string(j,'$.url'),''))),
                           '#.*$','') AS canonical_url,
            regexp_replace(lower(trim(COALESCE(text,''))),
                           '[^[:alnum:]]+',' ','g') AS normalized_text
          FROM typed
        ), identified AS (
          SELECT *,sha256(COALESCE(text,'')) AS exact_text_hash,
            sha256(normalized_text) AS normalized_text_hash,
            CASE WHEN canonical_url<>'' THEN sha256(canonical_url)
                 ELSE sha256('TEXT_ONLY:'||sha256(COALESCE(text,''))) END AS document_id,
            lower(regexp_extract(canonical_url,'^https?://(?:www\\.)?([^/:?#]+)',1))
              AS document_domain,
            CASE
              WHEN source_clock_ts IS NULL THEN 'UNPARSEABLE'
              WHEN lower(COALESCE(time_precision,''))='day' THEN 'COARSE_DAY'
              WHEN lower(COALESCE(time_precision,''))='minute'
               AND EXTRACT(hour FROM source_clock_ts)=0
               AND EXTRACT(minute FROM source_clock_ts)=0
               AND EXTRACT(second FROM source_clock_ts)=0 THEN 'SUSPECT_MIDNIGHT'
              WHEN lower(COALESCE(time_precision,''))='minute'
               AND COALESCE(tz_hint,'') IN ('UTC','America/New_York')
                THEN 'EXACT_TIME_CANDIDATE'
              ELSE 'TIMEZONE_AMBIGUOUS' END AS time_class,
            CASE
              WHEN tz_hint='America/New_York' THEN timezone('America/New_York',source_clock_ts)
              WHEN upper(COALESCE(tz_hint,''))='UTC' THEN timezone('UTC',source_clock_ts)
              ELSE TRY_CAST(raw_date AS TIMESTAMPTZ) END AS published_at_proxy_utc
          FROM fields
        ), versioned AS (
          SELECT *,sha256(document_id||chr(31)||exact_text_hash) AS document_version_id,
            sha256('STORY_EXACT_NORMALIZED:'||normalized_text_hash) AS story_candidate_id,
            CAST(source_clock_ts AS DATE) AS source_calendar_day
          FROM identified
        )
        SELECT v.*,substr(document_version_id,1,1) AS identity_bucket,
          sha256(story_candidate_id||chr(31)||
                   COALESCE(CAST(source_calendar_day AS VARCHAR),'UNKNOWN_DAY'))
                   AS episode_candidate_id,
          sha256(document_version_id||chr(31)||COALESCE(collection_source,'')||chr(31)||
                 COALESCE(dataset_source,'')||chr(31)||COALESCE(publisher_raw,'')||chr(31)||
                 COALESCE(source_raw,'')||chr(31)||COALESCE(raw_date,'')||chr(31)||
                 COALESCE(time_precision,'')||chr(31)||COALESCE(tz_hint,'')||chr(31)||
                 COALESCE(filename,'')) AS evidence_id,
          m.same_session,m.next_session,
          CASE
            WHEN time_class='EXACT_TIME_CANDIDATE' AND m.same_session IS NOT NULL
             AND CAST(source_clock_ts AS TIME)<TIME '16:00:00' THEN m.same_session
            WHEN time_class IN ('EXACT_TIME_CANDIDATE','COARSE_DAY','SUSPECT_MIDNIGHT',
                                'TIMEZONE_AMBIGUOUS') THEN m.next_session
            ELSE NULL END AS conservative_available_session,
          CASE
            WHEN time_class='EXACT_TIME_CANDIDATE'
              THEN 'HISTORICAL_MINUTE_PROXY_NOT_STRICT_PIT'
            WHEN time_class IN ('COARSE_DAY','SUSPECT_MIDNIGHT')
              THEN 'NEXT_SESSION_PROXY_ONLY'
            ELSE 'RECONSTRUCTION_ONLY' END AS predictive_use_status,
          COALESCE(EXTRACT(year FROM source_clock_ts)::INTEGER,0) AS publication_year,
          false AS historical_strict_pit,false AS intraday_feature_allowed,
          true AS reconstruction_allowed,true AS posthoc_explanation_allowed,
          'NOT_INFERRED_OUTCOME_SIDE_ONLY' AS market_impact_t0_status
        FROM versioned v LEFT JOIN calendar_map m
          ON m.calendar_day=CAST(v.source_clock_ts AS DATE)
        """
    )


def _news_queries(source: str = "news_parsed") -> dict[str, tuple[str, list[str]]]:
    documents = f"""
      SELECT MIN(publication_year) AS publication_year,
        document_id,document_version_id,story_candidate_id,
        canonical_url,document_domain,exact_text_hash,normalized_text_hash,
        ANY_VALUE(text) AS text,MIN(length(text)) AS min_text_chars,
        MAX(length(text)) AS max_text_chars,COUNT(*) AS expanded_rows,
        COUNT(DISTINCT evidence_id) AS evidence_records,
        COUNT(DISTINCT collection_source) AS collection_count,
        MIN(published_at_proxy_utc) AS first_publication_proxy_utc,
        MAX(published_at_proxy_utc) AS last_publication_proxy_utc,
        'LOCAL_RESEARCH_ONLY_NO_REDISTRIBUTION' AS rights_status
      FROM {source}
      GROUP BY document_id,document_version_id,story_candidate_id,
               canonical_url,document_domain,exact_text_hash,normalized_text_hash
    """
    evidence = f"""
      SELECT publication_year,evidence_id,document_id,document_version_id,
        story_candidate_id,episode_candidate_id,collection_source,dataset_source,
        publisher_raw,source_raw,canonical_url,document_domain,raw_date,
        source_clock_ts,published_at_proxy_utc,time_precision,tz_hint,time_class,
        source_calendar_day,conservative_available_session,predictive_use_status,
        historical_strict_pit,intraday_feature_allowed,reconstruction_allowed,
        posthoc_explanation_allowed,market_impact_t0_status,filename,
        COUNT(*) AS expanded_rows
      FROM {source}
      GROUP BY ALL
    """
    links = f"""
      WITH expanded AS (
        SELECT publication_year,document_version_id,
               upper(trim(symbol)) AS source_ticker
        FROM {source},UNNEST(TRY_CAST(stocks_json AS VARCHAR[])) s(symbol)
        WHERE symbol IS NOT NULL AND trim(symbol)<>''
      )
      SELECT MIN(e.publication_year) AS publication_year,e.document_version_id,
             e.source_ticker,c.asset_id,c.sector,
        CASE WHEN c.asset_id IS NULL THEN 'UNRESOLVED_SOURCE_TICKER'
             ELSE 'EXACT_CURRENT_SYMBOL_PROXY_NOT_HISTORICALLY_CANONICAL' END
          AS identity_status,COUNT(*) AS expanded_link_rows
      FROM expanded e LEFT JOIN core_assets c ON e.source_ticker=c.ticker
      GROUP BY e.document_version_id,e.source_ticker,c.asset_id,c.sector
    """
    return {
        "news_document_versions": (documents, ["publication_year"]),
        "news_collection_evidence": (evidence, ["publication_year"]),
        "news_asset_links": (links, ["publication_year"]),
    }


def _news_source_batches(
    root: Path, plan: Mapping[str, Any]
) -> list[tuple[str, str]]:
    """Bound parsing by source bytes, splitting unusually large files by ID hash."""
    target_bytes = 512 * 1024 ** 2
    groups: list[list[Mapping[str, Any]]] = []
    current: list[Mapping[str, Any]] = []
    current_bytes = 0
    for item in plan["snapshots_full"]["news"]["files"]:
        size = int(item["size_bytes"])
        if size > target_bytes:
            if current:
                groups.append(current)
                current, current_bytes = [], 0
            groups.append([item])
            continue
        if current and current_bytes + size > target_bytes:
            groups.append(current)
            current, current_bytes = [], 0
        current.append(item)
        current_bytes += size
    if current:
        groups.append(current)

    batches: list[tuple[str, str]] = []
    hex_digits = "0123456789abcdef"
    for group_index, group in enumerate(groups, start=1):
        filenames: set[str] = set()
        for item in group:
            path = Path(str(item["local_path"]))
            filenames.add(str(path))
            try:
                filenames.add(path.relative_to(root).as_posix())
            except ValueError:
                pass
        filename_filter = "filename IN (" + ",".join(
            _sql_string(value) for value in sorted(filenames)
        ) + ")"
        total_bytes = sum(int(item["size_bytes"]) for item in group)
        bucket_sets = [hex_digits]
        if total_bytes > target_bytes:
            bucket_sets = [hex_digits[index:index + 4] for index in range(0, 16, 4)]
        for shard_index, buckets in enumerate(bucket_sets, start=1):
            paths = [str(item["local_path"]) for item in group]
            digest = sha256_bytes(canonical_json_bytes({
                "paths": paths, "identity_buckets": buckets,
            }))[:10]
            label = f"source_{group_index:03d}_{shard_index:02d}_{digest}"
            bucket_filter = ",".join(_sql_string(value) for value in buckets)
            batches.append((
                label,
                f"SELECT * FROM news_parsed WHERE {filename_filter} "
                f"AND identity_bucket IN ({bucket_filter})",
            ))
    return batches


def _partition_bucket_sources(
    artifact: Path, partition_column: str
) -> dict[str, str]:
    buckets = sorted({
        path.name.split("=", 1)[1]
        for path in artifact.glob(f"_batches/*/{partition_column}=*")
        if path.is_dir() and "=" in path.name
    })
    result: dict[str, str] = {}
    for bucket in buckets:
        pattern = (
            artifact / "_batches" / "*" / f"{partition_column}={bucket}"
            / "**" / "*.parquet"
        ).as_posix()
        result[bucket] = (
            f"read_parquet({_sql_string(pattern)},union_by_name=true,"
            "hive_partitioning=true)"
        )
    return result


def _batch_scan(artifact: Path, label: str) -> str | None:
    batch = artifact / "_batches" / label
    if not any(batch.rglob("*.parquet")):
        return None
    return _artifact_scan(batch)


def _story_input_query(docs: str, links: str | None) -> str:
    link_source = links or """(
      SELECT CAST(NULL AS VARCHAR) AS document_version_id,
             CAST(NULL AS BIGINT) AS asset_id WHERE false
    )"""
    return f"""
      WITH link_counts AS (
        SELECT document_version_id,COUNT(*) AS asset_links,
               COUNT(DISTINCT asset_id) FILTER(WHERE asset_id IS NOT NULL)
                 AS exact_core_assets
        FROM {link_source} GROUP BY document_version_id
      )
      SELECT substr(d.story_candidate_id,1,1) AS story_bucket,
        d.publication_year,d.story_candidate_id,d.document_version_id,
        d.document_id,d.document_domain,d.expanded_rows,
        COALESCE(l.asset_links,0) AS asset_links,
        COALESCE(l.exact_core_assets,0) AS exact_core_assets,
        d.first_publication_proxy_utc,d.last_publication_proxy_utc
      FROM {docs} d LEFT JOIN link_counts l USING(document_version_id)
    """


def _story_candidate_query(source: str) -> str:
    return f"""
      SELECT MIN(publication_year) AS publication_year,story_candidate_id,
        COUNT(*) AS document_versions,COUNT(DISTINCT document_id) AS documents,
        COUNT(DISTINCT document_domain) FILTER(WHERE document_domain<>'') AS domains,
        SUM(expanded_rows) AS expanded_rows,SUM(asset_links) AS asset_links,
        SUM(exact_core_assets) AS exact_core_asset_links,
        MIN(first_publication_proxy_utc) AS first_publication_proxy_utc,
        MAX(last_publication_proxy_utc) AS last_publication_proxy_utc,
        'EXACT_NORMALIZED_TEXT_CANDIDATE_NOT_SEMANTIC_EVENT_IDENTITY'
          AS story_status
      FROM {source} GROUP BY story_candidate_id
    """


def _episode_input_query(evidence: str) -> str:
    return f"""
      SELECT substr(episode_candidate_id,1,1) AS episode_bucket,
        episode_candidate_id,story_candidate_id,source_calendar_day,
        document_version_id,document_domain,collection_source,
        published_at_proxy_utc,conservative_available_session,expanded_rows
      FROM {evidence}
    """


def _episode_candidate_query(source: str) -> str:
    return f"""
      SELECT COALESCE(EXTRACT(year FROM source_calendar_day)::INTEGER,0)
               AS publication_year,
        episode_candidate_id,story_candidate_id,source_calendar_day,
        COUNT(DISTINCT document_version_id) AS document_versions,
        COUNT(DISTINCT document_domain) FILTER(WHERE document_domain<>'') AS domains,
        COUNT(DISTINCT collection_source) AS collections,
        MIN(published_at_proxy_utc) AS public_time_lower_proxy_utc,
        MAX(published_at_proxy_utc) AS public_time_upper_proxy_utc,
        MIN(conservative_available_session) AS first_conservative_session,
        SUM(expanded_rows) AS expanded_rows,
        'EXACT_NORMALIZED_STORY_PLUS_DAY_CANDIDATE_NOT_CANONICAL_EVENT'
          AS episode_status,
        'NOT_INFERRED_OUTCOME_SIDE_ONLY' AS market_impact_t0_status,
        false AS model_visible
      FROM {source}
      GROUP BY episode_candidate_id,story_candidate_id,source_calendar_day
    """


def _remove_news_internal(path: Path, lake: Path) -> None:
    if not path.exists():
        return
    if path.parent != lake or not path.name.startswith(".news_"):
        raise CanonicalLakeError(f"refusing to remove unexpected internal path: {path}")
    shutil.rmtree(path)


def materialize_news(root: Path, config: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    gate = storage_gate(root, config, "news")
    if not gate["pass"]:
        raise CanonicalLakeError(f"news storage gate failed: {gate}")
    report_dir = Path(plan["report_path"])
    marker_path = report_dir / "news_stage.json"
    if marker_path.exists():
        marker = read_json(marker_path)
        if marker.get("build_id") == plan["build_id"] and marker.get("status") == "PASS_STAGE_MATERIALIZED":
            return {**marker, "idempotent_reuse": True}
    run_id = _stage_start(root, config, plan, "news")
    before = _input_state(root, config, plan["snapshots_full"])
    artifacts: dict[str, Any] = {}
    lake = Path(plan["lake_path"])
    lake.mkdir(parents=True, exist_ok=True)
    parsed_shards = lake / ".news_parsed_identity_shards"
    story_inputs = lake / ".news_story_inputs"
    episode_inputs = lake / ".news_episode_inputs"
    try:
        with tempfile.TemporaryDirectory(prefix="_duckdb_news_", dir=lake) as spill:
            connection = _connect(root, config, plan, Path(spill))
            try:
                primary_names = list(_news_queries().keys())
                primary_complete = all(
                    (lake / name / "_SUCCESS.json").exists() for name in primary_names
                )
                if not primary_complete:
                    _create_news_parsed(connection, config)
                    _copy_dataset_by_query_batches(
                        connection, _news_source_batches(root, plan), parsed_shards,
                        ["identity_bucket"], config, plan["build_id"],
                        "news_parsed_identity_shards_internal",
                        "NEWS_SOURCE_BATCH_COMPLETE",
                        "SOURCE_FILE_BYTES_WITH_LARGE_FILE_IDENTITY_SUBSHARDS",
                    )
                    identity_sources = _partition_bucket_sources(
                        parsed_shards, "identity_bucket"
                    )
                    if not identity_sources:
                        raise CanonicalLakeError("news identity shards are empty")
                    for name in primary_names:
                        batches = [
                            (
                                f"identity_{bucket}",
                                _news_queries(source)[name][0],
                            )
                            for bucket, source in identity_sources.items()
                        ]
                        partitions = _news_queries()[name][1]
                        state, count, reused = _copy_dataset_by_query_batches(
                            connection, batches, lake / name, partitions, config,
                            plan["build_id"], name,
                            "NEWS_IDENTITY_BATCH_COMPLETE",
                            "DOCUMENT_VERSION_IDENTITY_HASH_BUCKET",
                        )
                        artifacts[name] = {
                            "tree_state": state, "row_count": count, "reused": reused
                        }
                        _record_artifact(root, config, plan, "news", name, state, count)
                    _remove_news_internal(parsed_shards, lake)
                else:
                    for name in primary_names:
                        marker = read_json(lake / name / "_SUCCESS.json")
                        state = _tree_state(lake / name, hash_files=True)
                        artifacts[name] = {
                            "tree_state": state,
                            "row_count": int(marker["row_count"]),
                            "reused": True,
                        }

                name = "news_story_candidates"
                if not (lake / name / "_SUCCESS.json").exists():
                    identity_labels = sorted(
                        path.name for path in (lake / "news_document_versions" / "_batches").iterdir()
                        if path.is_dir()
                    )
                    story_input_batches = []
                    for label in identity_labels:
                        docs_batch = _batch_scan(lake / "news_document_versions", label)
                        if docs_batch is None:
                            continue
                        links_batch = _batch_scan(lake / "news_asset_links", label)
                        story_input_batches.append((
                            label, _story_input_query(docs_batch, links_batch)
                        ))
                    _copy_dataset_by_query_batches(
                        connection, story_input_batches, story_inputs,
                        ["story_bucket"], config, plan["build_id"],
                        "news_story_inputs_internal", "NEWS_STORY_INPUT_BATCH_COMPLETE",
                        "DOCUMENT_IDENTITY_TO_STORY_HASH_REPARTITION",
                    )
                    story_sources = _partition_bucket_sources(story_inputs, "story_bucket")
                    story_batches = [
                        (f"story_{bucket}", _story_candidate_query(source))
                        for bucket, source in story_sources.items()
                    ]
                    state, count, reused = _copy_dataset_by_query_batches(
                        connection, story_batches, lake / name, ["publication_year"],
                        config, plan["build_id"], name,
                        "NEWS_STORY_BATCH_COMPLETE", "STORY_IDENTITY_HASH_BUCKET",
                    )
                    _remove_news_internal(story_inputs, lake)
                else:
                    marker = read_json(lake / name / "_SUCCESS.json")
                    state, count, reused = (
                        _tree_state(lake / name, hash_files=True),
                        int(marker["row_count"]), True,
                    )
                artifacts[name] = {"tree_state": state, "row_count": count, "reused": reused}
                _record_artifact(root, config, plan, "news", name, state, count)
                name = "information_episode_candidates"
                if not (lake / name / "_SUCCESS.json").exists():
                    identity_labels = sorted(
                        path.name for path in (lake / "news_collection_evidence" / "_batches").iterdir()
                        if path.is_dir()
                    )
                    episode_input_batches = []
                    for label in identity_labels:
                        evidence_batch = _batch_scan(lake / "news_collection_evidence", label)
                        if evidence_batch is not None:
                            episode_input_batches.append((
                                label, _episode_input_query(evidence_batch)
                            ))
                    _copy_dataset_by_query_batches(
                        connection, episode_input_batches, episode_inputs,
                        ["episode_bucket"], config, plan["build_id"],
                        "news_episode_inputs_internal",
                        "NEWS_EPISODE_INPUT_BATCH_COMPLETE",
                        "DOCUMENT_IDENTITY_TO_EPISODE_HASH_REPARTITION",
                    )
                    episode_sources = _partition_bucket_sources(
                        episode_inputs, "episode_bucket"
                    )
                    episode_batches = [
                        (f"episode_{bucket}", _episode_candidate_query(source))
                        for bucket, source in episode_sources.items()
                    ]
                    state, count, reused = _copy_dataset_by_query_batches(
                        connection, episode_batches, lake / name, ["publication_year"],
                        config, plan["build_id"], name,
                        "NEWS_EPISODE_BATCH_COMPLETE", "EPISODE_IDENTITY_HASH_BUCKET",
                    )
                    _remove_news_internal(episode_inputs, lake)
                else:
                    marker = read_json(lake / name / "_SUCCESS.json")
                    state, count, reused = (
                        _tree_state(lake / name, hash_files=True),
                        int(marker["row_count"]), True,
                    )
                artifacts[name] = {"tree_state": state, "row_count": count, "reused": reused}
                _record_artifact(root, config, plan, "news", name, state, count)
            finally:
                connection.close()
        after = _input_state(root, config, plan["snapshots_full"])
        marker = _write_stage_marker(marker_path, plan, "news", artifacts, before == after)
        if marker["status"] != "PASS_STAGE_MATERIALIZED":
            raise CanonicalLakeError("source inputs changed during news materialization")
        _stage_finish(root, config, plan, run_id, "news", "COMPLETED", marker_path)
        return marker
    except Exception as exc:
        _stage_finish(root, config, plan, run_id, "news", "FAILED", error={"error": str(exc)})
        raise


def _report(connection: Any, sql: str) -> list[dict[str, Any]]:
    return _rows(connection.execute(sql))


def audit_build(root: Path, config: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    report_dir = Path(plan["report_path"])
    bars_marker = report_dir / "bars_stage.json"
    news_marker = report_dir / "news_stage.json"
    if not bars_marker.exists() or not news_marker.exists():
        raise CanonicalLakeError("bars and news stages must both pass before audit")
    for path in (bars_marker, news_marker):
        marker = read_json(path)
        if marker.get("build_id") != plan["build_id"] or marker.get("status") != "PASS_STAGE_MATERIALIZED":
            raise CanonicalLakeError(f"invalid stage marker: {path}")
    run_id = _stage_start(root, config, plan, "audit")
    before = _input_state(root, config, plan["snapshots_full"])
    lake = Path(plan["lake_path"])
    reports: dict[str, Any] = {}
    try:
        with tempfile.TemporaryDirectory(prefix="_duckdb_audit_", dir=lake) as spill:
            connection = _connect(root, config, plan, Path(spill))
            try:
                bars = _artifact_scan(lake / "bar_sessions")
                reconciliation = _artifact_scan(lake / "bar_source_reconciliation")
                docs = _artifact_scan(lake / "news_document_versions")
                evidence = _artifact_scan(lake / "news_collection_evidence")
                links = _artifact_scan(lake / "news_asset_links")
                stories = _artifact_scan(lake / "news_story_candidates")
                episodes = _artifact_scan(lake / "information_episode_candidates")
                reports["bar_session_report.json"] = {
                    "version": VERSION,
                    "status": "PASS_SESSION_LAYER_REVIEW_READY",
                    "summary": _one(connection.execute(f"""
                      SELECT COUNT(*) AS asset_days,COUNT(DISTINCT ticker) AS tickers,
                        COUNT(DISTINCT asset_id) FILTER(WHERE asset_id IS NOT NULL) AS core_assets,
                        MIN(trading_day) AS min_day,MAX(trading_day) AS max_day,
                        SUM(rth_completeness_status='FULL_390_PROXY') AS full_390_days,
                        SUM(rth_completeness_status='PARTIAL_OR_SHORT_SESSION_REVIEW')
                          AS partial_or_short_days,
                        SUM(source_rows<>distinct_minutes) AS duplicate_minute_days
                      FROM {bars}
                    """)),
                    "training_authorized": False,
                }
                reports["bar_reconciliation_report.json"] = {
                    "version": VERSION,
                    "status": "PASS_SOURCE_VALUES_PRESERVED_REVIEW_REQUIRED",
                    "by_status": _report(connection, f"""
                      SELECT reconciliation_status,COUNT(*) AS asset_days,
                        QUANTILE_CONT(ABS(close_return_diff_pct_points),0.5)
                          AS median_abs_return_diff_pp,
                        QUANTILE_CONT(ABS(close_return_diff_pct_points),0.95)
                          AS p95_abs_return_diff_pp
                      FROM {reconciliation} GROUP BY reconciliation_status
                      ORDER BY asset_days DESC
                    """),
                    "cross_source_policy": config["bars"]["cross_source_policy"],
                    "training_authorized": False,
                }
                reports["midnight_forensics_report.json"] = {
                    "version": VERSION,
                    "status": "REVIEW_MIDNIGHT_IS_COARSE_NOT_DISCARDED",
                    "by_collection_domain_year": _report(connection, f"""
                      SELECT collection_source,document_domain,publication_year,time_class,
                             COUNT(*) AS evidence_records,SUM(expanded_rows) AS expanded_rows
                      FROM {evidence} GROUP BY 1,2,3,4 ORDER BY expanded_rows DESC
                    """),
                    "conflicting_clock_documents": _one(connection.execute(f"""
                      WITH x AS (
                        SELECT document_version_id,
                          SUM(time_class='SUSPECT_MIDNIGHT') AS midnight_evidence,
                          SUM(time_class='EXACT_TIME_CANDIDATE') AS exact_evidence
                        FROM {evidence} GROUP BY document_version_id)
                      SELECT COUNT(*) FILTER(WHERE midnight_evidence>0 AND exact_evidence>0)
                               AS documents_with_midnight_and_exact_evidence,
                             COUNT(*) FILTER(WHERE midnight_evidence>0)
                               AS documents_with_midnight_evidence
                      FROM x
                    """)),
                    "policy": config["news"]["midnight_policy"],
                    "training_authorized": False,
                }
                reports["document_identity_report.json"] = {
                    "version": VERSION,
                    "status": "PASS_DOCUMENT_VERSION_LINK_UNITS_SEPARATED",
                    "counts": {
                        "document_versions": _one(connection.execute(f"SELECT COUNT(*) n FROM {docs}"))["n"],
                        "documents": _one(connection.execute(f"SELECT COUNT(DISTINCT document_id) n FROM {docs}"))["n"],
                        "collection_evidence": _one(connection.execute(f"SELECT COUNT(*) n FROM {evidence}"))["n"],
                        "asset_links": _one(connection.execute(f"SELECT COUNT(*) n FROM {links}"))["n"],
                        "story_candidates": _one(connection.execute(f"SELECT COUNT(*) n FROM {stories}"))["n"],
                        "episode_candidates": _one(connection.execute(f"SELECT COUNT(*) n FROM {episodes}"))["n"],
                        "raw_expanded_rows": _one(connection.execute(f"SELECT SUM(expanded_rows) n FROM {docs}"))["n"],
                    },
                    "story_identity_is_canonical": False,
                    "episode_identity_is_canonical": False,
                    "training_authorized": False,
                }
                reports["url_version_report.json"] = {
                    "version": VERSION,
                    "status": "PASS_URL_VERSIONS_PRESERVED",
                    "summary": _one(connection.execute(f"""
                      WITH x AS (SELECT document_id,COUNT(*) versions
                        FROM {docs} GROUP BY document_id)
                      SELECT COUNT(*) AS documents,SUM(versions>1) AS multi_version_documents,
                        MAX(versions) AS maximum_versions,
                        QUANTILE_CONT(versions,0.99) AS p99_versions FROM x
                    """)),
                    "training_authorized": False,
                }
                reports["causal_clock_report.json"] = {
                    "version": VERSION,
                    "status": "PASS_CLOCK_ROLES_SEPARATED_MODEL_VISIBILITY_BLOCKED",
                    "evidence_by_time_class": _report(connection, f"""
                      SELECT time_class,predictive_use_status,COUNT(*) AS evidence_records,
                        SUM(expanded_rows) AS expanded_rows,
                        SUM(conservative_available_session IS NOT NULL) AS with_session_proxy
                      FROM {evidence} GROUP BY 1,2 ORDER BY expanded_rows DESC
                    """),
                    "publication_equals_market_impact_t0": False,
                    "market_impact_t0_role": config["news"]["market_impact_t0_policy"],
                    "historical_strict_pit": False,
                    "posthoc_evidence_can_precede_its_article": True,
                    "future_document_can_be_earlier_feature": False,
                    "training_authorized": False,
                }
                reports["identity_coverage_report.json"] = {
                    "version": VERSION,
                    "status": "REVIEW_CURRENT_SYMBOL_PROXY_ONLY",
                    "bars": _one(connection.execute(f"""
                      SELECT COUNT(DISTINCT ticker) AS source_tickers,
                        COUNT(DISTINCT asset_id) FILTER(WHERE asset_id IS NOT NULL)
                          AS exact_current_core_assets
                      FROM {bars}
                    """)),
                    "news": _one(connection.execute(f"""
                      SELECT COUNT(DISTINCT source_ticker) AS source_tickers,
                        COUNT(DISTINCT asset_id) FILTER(WHERE asset_id IS NOT NULL)
                          AS exact_current_core_assets,
                        COUNT(*) FILTER(WHERE asset_id IS NULL) AS unresolved_links
                      FROM {links}
                    """)),
                    "graph_role": config["identity"]["graph_role"],
                    "training_authorized": False,
                }
            finally:
                connection.close()
        after = _input_state(root, config, plan["snapshots_full"])
        unchanged = before == after
        report_hashes = {}
        for name, payload in reports.items():
            report_hashes[name] = _atomic_json(report_dir / name, payload)
        storage = {
            "managed_bytes": _managed_size(root, config),
            "hard_cap_bytes": int(config["storage"]["hard_cap_bytes"]),
            "lake_build_bytes": _directory_size(lake),
            "pass": _managed_size(root, config) <= int(config["storage"]["hard_cap_bytes"]),
            "preallocation_performed": False,
        }
        report_hashes["storage_report.json"] = _atomic_json(report_dir / "storage_report.json", storage)
        failures = []
        if not unchanged:
            failures.append("source_inputs_changed_during_audit")
        if not storage["pass"]:
            failures.append("storage_cap_exceeded")
        audit = {
            "version": VERSION,
            "build_id": plan["build_id"],
            "status": "PASS_CANONICAL_LAKE_REVIEW_READY" if not failures
                      else "FAIL_CANONICAL_LAKE_AUDIT",
            "input_state_unchanged": unchanged,
            "structural_failures": failures,
            "report_sha256": report_hashes,
            "scientific_blockers_before_training": [
                "Historical news remains strict_pit=false.",
                "Story and episode identities are deterministic candidates, not semantic truth.",
                "Historical ticker validity remains unresolved for current-symbol proxies.",
                "Market-impact t0 has not been inferred and may never be a feature.",
                "Bar feed and adjustment regimes remain source-specific.",
                "A separate preregistered incremental-information test is required.",
            ],
            "training_authorized": False,
            "feature_visibility": FEATURE_VISIBILITY,
            "v009_interaction": "NONE",
            "interpretation": (
                "PASS means deterministic units, clocks and source-specific session bars "
                "are reviewable. It does not authorize a predictive feature or model."
            ),
        }
        audit_sha = _atomic_json(report_dir / "audit.json", audit)
        audit["audit_file_sha256"] = audit_sha
        _stage_finish(
            root, config, plan, run_id, "audit",
            "COMPLETED" if not failures else "FAILED_GATE",
            report_dir / "audit.json",
        )
        return audit
    except Exception as exc:
        _stage_finish(root, config, plan, run_id, "audit", "FAILED", error={"error": str(exc)})
        raise


def persist_plan(root: Path, config: Mapping[str, Any], plan: Mapping[str, Any]) -> dict[str, Any]:
    _register_build(root, config, plan)
    report_dir = Path(plan["report_path"])
    payload = dict(plan)
    payload.pop("snapshots_full", None)
    digest = _atomic_json(report_dir / "plan.json", payload)
    return {**payload, "plan_file_sha256": digest}


def main() -> None:
    root = Path(__file__).resolve().parents[1]
    parser = argparse.ArgumentParser(description="Public Information Canonical Lake V002")
    parser.add_argument("--config", default=str(root / "config" / f"{VERSION}.json"))
    parser.add_argument("--stage", choices=["plan", "bars", "news", "audit", "all"],
                        default="plan")
    args = parser.parse_args()
    config = load_config(root, Path(args.config).resolve())
    plan = build_plan(root, config)
    persisted = persist_plan(root, config, plan)
    if args.stage == "plan":
        result = persisted
    elif args.stage == "bars":
        print(json.dumps({"status": "STARTING_BARS", "build_id": plan["build_id"]}), flush=True)
        result = materialize_bars(root, config, plan)
    elif args.stage == "news":
        print(json.dumps({"status": "STARTING_NEWS", "build_id": plan["build_id"]}), flush=True)
        result = materialize_news(root, config, plan)
    elif args.stage == "audit":
        result = audit_build(root, config, plan)
    else:
        print(json.dumps({"status": "STARTING_BARS", "build_id": plan["build_id"]}), flush=True)
        materialize_bars(root, config, plan)
        print(json.dumps({"status": "STARTING_NEWS", "build_id": plan["build_id"]}), flush=True)
        materialize_news(root, config, plan)
        result = audit_build(root, config, plan)
    print(json.dumps(_jsonable(result), indent=2, sort_keys=True, ensure_ascii=False))


if __name__ == "__main__":
    main()

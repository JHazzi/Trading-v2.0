from __future__ import annotations

import fnmatch
import hashlib
import json
import os
import re
import shutil
import sqlite3
import tempfile
import time
import uuid
from contextlib import closing, contextmanager
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, BinaryIO, Callable, Iterable, Iterator, Mapping
from urllib.error import HTTPError, URLError
from urllib.parse import quote
from urllib.request import Request, urlopen


GIB = 1024**3
ABSOLUTE_STORAGE_CAP_BYTES = 100 * GIB
CONTRACT_VERSION = "public_information_intake_v001"
MODEL_VISIBILITY = (
    "BLOCKED_UNTIL_SEPARATE_POINT_IN_TIME_MATERIALIZER_AND_"
    "PREREGISTERED_INCREMENT_TEST"
)
HF_BASE_URL = "https://huggingface.co"
USER_AGENT = "quant-market-ai-public-information-intake-v001"


class IntakeError(RuntimeError):
    """An explicit acquisition, integrity or isolation gate failed."""


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def canonical_json_bytes(payload: Any) -> bytes:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        ensure_ascii=False,
        allow_nan=False,
    ).encode("utf-8")


def sha256_bytes(payload: bytes) -> str:
    return hashlib.sha256(payload).hexdigest()


def sha256_file(path: Path, chunk_bytes: int = 8 * 1024 * 1024) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while True:
            chunk = handle.read(chunk_bytes)
            if not chunk:
                break
            digest.update(chunk)
    return digest.hexdigest()


def read_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _is_relative_to(path: Path, parent: Path) -> bool:
    try:
        path.relative_to(parent)
        return True
    except ValueError:
        return False


def resolve_project_path(project_root: Path, raw_path: str | Path) -> Path:
    path = Path(raw_path)
    if not path.is_absolute():
        path = project_root / path
    return path.resolve(strict=False)


def _managed_roots(project_root: Path, config: Mapping[str, Any]) -> list[Path]:
    return [
        resolve_project_path(project_root, path)
        for path in config["storage"]["managed_roots"]
    ]


def validate_write_path(
    project_root: Path, config: Mapping[str, Any], path: Path
) -> Path:
    resolved = path.resolve(strict=False)
    paths = config["paths"]
    allowed_roots = [
        resolve_project_path(project_root, paths["raw_root"]),
        resolve_project_path(project_root, paths["lake_root"]),
        resolve_project_path(project_root, paths["report_root"]),
    ]
    catalog = resolve_project_path(project_root, paths["catalog_db"])
    if resolved != catalog and not any(
        _is_relative_to(resolved, root) for root in allowed_roots
    ):
        raise IntakeError(f"write path is outside intake roots: {resolved}")

    isolation = config["isolation"]
    protected = [
        resolve_project_path(project_root, item)
        for item in isolation["protected_write_paths"]
    ]
    for item in protected:
        if resolved == item or _is_relative_to(resolved, item):
            raise IntakeError(f"write path intersects protected V009/source path: {resolved}")
    lowered = str(resolved).lower()
    for token in isolation["forbidden_write_path_tokens"]:
        if token.lower() in lowered:
            raise IntakeError(f"write path contains protected token {token!r}: {resolved}")
    return resolved


def validate_config(config: Mapping[str, Any]) -> dict[str, Any]:
    errors: list[str] = []
    if config.get("contract_version") != CONTRACT_VERSION:
        errors.append("contract_version mismatch")
    if config.get("training_authorized") is not False:
        errors.append("training_authorized must be false")
    if config.get("feature_visibility") != MODEL_VISIBILITY:
        errors.append("feature_visibility must remain blocked")

    storage = config.get("storage", {})
    hard_cap = int(storage.get("hard_cap_bytes", 0))
    absolute = int(storage.get("absolute_max_allowed_bytes", 0))
    if hard_cap <= 0:
        errors.append("storage hard cap must be positive")
    if hard_cap > ABSOLUTE_STORAGE_CAP_BYTES:
        errors.append("storage hard cap exceeds the immutable 100 GiB ceiling")
    if absolute != ABSOLUTE_STORAGE_CAP_BYTES:
        errors.append("absolute_max_allowed_bytes must equal exactly 100 GiB")
    if storage.get("preallocate_space") is not False:
        errors.append("space preallocation is forbidden")
    if int(storage.get("minimum_free_after_operation_bytes", -1)) < 0:
        errors.append("minimum free-space guard cannot be negative")

    paths = config.get("paths", {})
    required_paths = {
        "catalog_db",
        "catalog_schema",
        "raw_root",
        "lake_root",
        "report_root",
    }
    missing_paths = sorted(required_paths - set(paths))
    if missing_paths:
        errors.append(f"missing configured paths: {missing_paths}")

    policies = config.get("economic_policies", {})
    if policies.get("cross_source_price_policy") != (
        "PRESERVE_EACH_SOURCE_NO_MEDIAN_NO_OVERWRITE"
    ):
        errors.append("cross-source prices must not be median-blended or overwritten")
    if policies.get("volume_policy") != "NEVER_BLEND_ACROSS_IEX_SIP_YAHOO":
        errors.append("volume policy must preserve feed-specific observations")

    datasets = config.get("datasets", {})
    if not datasets:
        errors.append("at least one dataset must be declared")
    for key, dataset in datasets.items():
        if dataset.get("repo_type") != "dataset":
            errors.append(f"{key}: repo_type must be dataset")
        if not dataset.get("repo_id") or not dataset.get("revision"):
            errors.append(f"{key}: repo_id and revision are required")
        if not dataset.get("profiles"):
            errors.append(f"{key}: at least one profile is required")
        for name, profile in dataset.get("profiles", {}).items():
            if not profile.get("include"):
                errors.append(f"{key}/{name}: include patterns are required")
            if int(profile.get("estimated_bytes", -1)) < 0:
                errors.append(f"{key}/{name}: estimated_bytes cannot be negative")

    return {
        "valid": not errors,
        "errors": errors,
        "hard_cap_bytes": hard_cap,
        "hard_cap_gib": hard_cap / GIB if hard_cap else 0.0,
        "preallocation": False,
        "training_authorized": False,
        "feature_visibility": MODEL_VISIBILITY,
    }


def load_config(path: Path) -> dict[str, Any]:
    config = read_json(path)
    validation = validate_config(config)
    if not validation["valid"]:
        raise IntakeError("invalid intake config: " + "; ".join(validation["errors"]))
    return config


def directory_size(path: Path) -> int:
    if not path.exists():
        return 0
    total = 0
    for root, dirs, files in os.walk(path, followlinks=False):
        dirs[:] = [name for name in dirs if not (Path(root) / name).is_symlink()]
        for name in files:
            file_path = Path(root) / name
            if not file_path.is_symlink():
                try:
                    total += file_path.stat().st_size
                except FileNotFoundError:
                    pass
    return total


def managed_storage_usage(project_root: Path, config: Mapping[str, Any]) -> int:
    return sum(directory_size(path) for path in _managed_roots(project_root, config))


def storage_gate(
    project_root: Path,
    config: Mapping[str, Any],
    additional_bytes: int,
) -> dict[str, Any]:
    additional = max(0, int(additional_bytes))
    usage = managed_storage_usage(project_root, config)
    cap = int(config["storage"]["hard_cap_bytes"])
    raw_root = resolve_project_path(project_root, config["paths"]["raw_root"])
    probe = raw_root if raw_root.exists() else project_root
    free = shutil.disk_usage(probe).free
    minimum_free = int(config["storage"]["minimum_free_after_operation_bytes"])
    projected = usage + additional
    gate = {
        "managed_usage_bytes": usage,
        "additional_bytes": additional,
        "projected_managed_bytes": projected,
        "hard_cap_bytes": cap,
        "filesystem_free_bytes": free,
        "minimum_free_after_operation_bytes": minimum_free,
        "preallocation_performed": False,
        "within_managed_cap": projected <= cap,
        "within_free_space_gate": free - additional >= minimum_free,
    }
    gate["pass"] = gate["within_managed_cap"] and gate["within_free_space_gate"]
    return gate


def _atomic_write(path: Path, payload: bytes) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with tempfile.NamedTemporaryFile(dir=path.parent, delete=False) as handle:
        temp_path = Path(handle.name)
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(temp_path, path)


def write_json(path: Path, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False
    ).encode("utf-8") + b"\n"
    _atomic_write(path, encoded)
    return sha256_bytes(encoded)


def write_immutable_json(path: Path, payload: Mapping[str, Any]) -> str:
    encoded = json.dumps(
        payload, indent=2, sort_keys=True, ensure_ascii=False, allow_nan=False
    ).encode("utf-8") + b"\n"
    if path.exists():
        existing = path.read_bytes()
        if existing != encoded:
            raise IntakeError(f"immutable manifest collision at {path}")
        return sha256_bytes(existing)
    _atomic_write(path, encoded)
    return sha256_bytes(encoded)


def report_path(
    project_root: Path,
    config: Mapping[str, Any],
    stage: str,
    dataset_key: str | None = None,
    snapshot_id: str | None = None,
) -> Path:
    root = resolve_project_path(project_root, config["paths"]["report_root"])
    date_part = datetime.now(timezone.utc).strftime("%Y%m%dT%H%M%S%fZ")
    pieces = [stage]
    if dataset_key:
        pieces.append(dataset_key)
    if snapshot_id:
        pieces.append(snapshot_id)
    path = root.joinpath(*pieces, f"{date_part}_{uuid.uuid4().hex[:8]}.json")
    return validate_write_path(project_root, config, path)


def _catalog_path(project_root: Path, config: Mapping[str, Any]) -> Path:
    return validate_write_path(
        project_root,
        config,
        resolve_project_path(project_root, config["paths"]["catalog_db"]),
    )


def _schema_path(project_root: Path, config: Mapping[str, Any]) -> Path:
    return resolve_project_path(project_root, config["paths"]["catalog_schema"])


def initialize_catalog(
    project_root: Path, config: Mapping[str, Any]
) -> dict[str, Any]:
    catalog = _catalog_path(project_root, config)
    schema_path = _schema_path(project_root, config)
    schema = schema_path.read_bytes()
    schema_hash = sha256_bytes(schema)
    catalog.parent.mkdir(parents=True, exist_ok=True)
    now = utc_now()
    with closing(sqlite3.connect(catalog)) as conn, conn:
        conn.executescript(schema.decode("utf-8"))
        row = conn.execute(
            "SELECT schema_sha256 FROM schema_migrations WHERE migration_id=?",
            (CONTRACT_VERSION,),
        ).fetchone()
        if row and row[0] != schema_hash:
            raise IntakeError("catalog schema changed under an applied migration id")
        conn.execute(
            "INSERT OR IGNORE INTO schema_migrations VALUES (?,?,?)",
            (CONTRACT_VERSION, now, schema_hash),
        )
        for key, dataset in config["datasets"].items():
            conn.execute(
                """
                INSERT INTO dataset_registry(
                  dataset_key,repo_type,repo_id,configured_revision,
                  declared_license,rights_status,causal_status,model_visibility,
                  updated_at_utc
                ) VALUES (?,?,?,?,?,?,?,?,?)
                ON CONFLICT(dataset_key) DO UPDATE SET
                  repo_type=excluded.repo_type,
                  repo_id=excluded.repo_id,
                  configured_revision=excluded.configured_revision,
                  declared_license=excluded.declared_license,
                  rights_status=excluded.rights_status,
                  causal_status=excluded.causal_status,
                  model_visibility=excluded.model_visibility,
                  updated_at_utc=excluded.updated_at_utc
                """,
                (
                    key,
                    dataset["repo_type"],
                    dataset["repo_id"],
                    dataset["revision"],
                    dataset.get("declared_license"),
                    dataset["rights_status"],
                    dataset["causal_status"],
                    MODEL_VISIBILITY,
                    now,
                ),
            )
    return {
        "status": "PASS_CATALOG_INITIALIZED",
        "catalog_db": str(catalog),
        "schema_sha256": schema_hash,
        "datasets_registered": sorted(config["datasets"]),
        "training_authorized": False,
        "v009_interaction": "NONE",
    }


def build_plan(project_root: Path, config: Mapping[str, Any]) -> dict[str, Any]:
    validation = validate_config(config)
    datasets: list[dict[str, Any]] = []
    total_estimate = 0
    initial_profiles = {
        (str(item[0]), str(item[1]))
        for item in config.get("initial_download_profiles", [])
    }
    for key, dataset in config["datasets"].items():
        profiles = []
        for name, profile in dataset["profiles"].items():
            estimate = int(profile["estimated_bytes"])
            profiles.append(
                {
                    "profile": name,
                    "include": profile["include"],
                    "exclude": profile.get("exclude", []),
                    "estimated_bytes": estimate,
                }
            )
            if (key, name) in initial_profiles:
                total_estimate += estimate
        datasets.append(
            {
                "dataset_key": key,
                "repo_id": dataset["repo_id"],
                "requested_revision": dataset["revision"],
                "declared_license": dataset.get("declared_license"),
                "rights_status": dataset["rights_status"],
                "causal_status": dataset["causal_status"],
                "profiles": profiles,
            }
        )
    storage = storage_gate(project_root, config, total_estimate)
    return {
        "contract_version": CONTRACT_VERSION,
        "status": "READY_FOR_MANIFESTS" if storage["pass"] else "BLOCKED_STORAGE",
        "config_validation": validation,
        "datasets": datasets,
        "storage": storage,
        "execution_order": [
            "init catalog",
            "freeze remote manifests",
            "download Alpaca bars",
            "audit and sample Alpaca bars",
            "download priority news subsets",
            "audit and sample priority news",
            "review before any full-news expansion",
        ],
        "economic_policies": config["economic_policies"],
        "training_authorized": False,
        "feature_visibility": MODEL_VISIBILITY,
        "v009_interaction": "NONE; protected paths are rejected by the write guard",
    }


def _token(dataset: Mapping[str, Any]) -> str | None:
    name = str(dataset.get("token_env", "HF_TOKEN"))
    value = os.environ.get(name)
    if value:
        return value.strip()
    if not dataset.get("token_optional", True):
        raise IntakeError(
            f"{name} is required for this gated dataset; the token is never persisted"
        )
    return None


def _headers(token: str | None, extra: Mapping[str, str] | None = None) -> dict[str, str]:
    headers = {"User-Agent": USER_AGENT, "Accept": "application/json"}
    if token:
        headers["Authorization"] = f"Bearer {token}"
    if extra:
        headers.update(extra)
    return headers


def _open_url(
    url: str,
    headers: Mapping[str, str],
    timeout: int,
):
    request = Request(url, headers=dict(headers))
    try:
        return urlopen(request, timeout=timeout)
    except HTTPError as exc:
        if exc.code in (401, 403):
            raise IntakeError(
                f"Hugging Face denied access ({exc.code}); verify HF_TOKEN and dataset access"
            ) from exc
        raise IntakeError(f"HTTP {exc.code} for {url}") from exc
    except URLError as exc:
        raise IntakeError(f"network error for {url}: {exc.reason}") from exc


def _json_url(url: str, token: str | None, timeout: int) -> tuple[Any, Mapping[str, str]]:
    with _open_url(url, _headers(token), timeout) as response:
        payload = response.read()
        headers = dict(response.headers.items())
    try:
        return json.loads(payload), headers
    except json.JSONDecodeError as exc:
        raise IntakeError(f"non-JSON response from {url}") from exc


_NEXT_LINK = re.compile(r'<([^>]+)>;\s*rel="next"')


def _next_link(headers: Mapping[str, str]) -> str | None:
    link = headers.get("Link") or headers.get("link") or ""
    match = _NEXT_LINK.search(link)
    return match.group(1) if match else None


def fetch_hf_metadata_and_tree(
    dataset: Mapping[str, Any], timeout: int = 60
) -> tuple[dict[str, Any], list[dict[str, Any]]]:
    repo_id = quote(str(dataset["repo_id"]), safe="/")
    revision = quote(str(dataset["revision"]), safe="")
    token = _token(dataset)
    info_url = f"{HF_BASE_URL}/api/datasets/{repo_id}/revision/{revision}"
    info, _ = _json_url(info_url, token, timeout)
    if not isinstance(info, dict) or not info.get("sha"):
        raise IntakeError("dataset metadata did not provide a resolved commit SHA")
    resolved = str(info["sha"])
    tree_url: str | None = (
        f"{HF_BASE_URL}/api/datasets/{repo_id}/tree/{quote(resolved, safe='')}"
        "?recursive=true"
    )
    entries: list[dict[str, Any]] = []
    seen_urls: set[str] = set()
    while tree_url:
        if tree_url in seen_urls:
            raise IntakeError("Hugging Face tree pagination loop detected")
        seen_urls.add(tree_url)
        page, headers = _json_url(tree_url, token, timeout)
        if not isinstance(page, list):
            raise IntakeError("Hugging Face tree response is not a list")
        entries.extend(item for item in page if isinstance(item, dict))
        tree_url = _next_link(headers)
    return info, entries


def _selected_tree_files(
    entries: Iterable[Mapping[str, Any]],
    include: Iterable[str],
    exclude: Iterable[str],
) -> list[dict[str, Any]]:
    includes = list(include)
    excludes = list(exclude)
    selected: list[dict[str, Any]] = []
    for entry in entries:
        path = str(entry.get("path", ""))
        entry_type = str(entry.get("type", ""))
        if entry_type not in ("file", "blob") or not path:
            continue
        if not any(fnmatch.fnmatchcase(path, pattern) for pattern in includes):
            continue
        if any(fnmatch.fnmatchcase(path, pattern) for pattern in excludes):
            continue
        size = entry.get("size")
        lfs = entry.get("lfs") or {}
        if size is None:
            size = lfs.get("size")
        if size is None or int(size) < 0:
            raise IntakeError(f"remote file has no trustworthy size: {path}")
        lfs_oid = str(lfs.get("oid", "")) or None
        lfs_sha = None
        if lfs_oid and lfs_oid.startswith("sha256:"):
            lfs_sha = lfs_oid.split(":", 1)[1]
        elif lfs_oid and re.fullmatch(r"[0-9a-fA-F]{64}", lfs_oid):
            lfs_sha = lfs_oid.lower()
        selected.append(
            {
                "repo_path": path,
                "size_bytes": int(size),
                "oid": entry.get("oid"),
                "lfs_sha256": lfs_sha,
                "xet_hash": entry.get("xetHash"),
            }
        )
    selected.sort(key=lambda row: row["repo_path"])
    return selected


def construct_manifest(
    dataset_key: str,
    profile_name: str,
    dataset: Mapping[str, Any],
    profile: Mapping[str, Any],
    metadata: Mapping[str, Any],
    entries: Iterable[Mapping[str, Any]],
) -> dict[str, Any]:
    selected = _selected_tree_files(
        entries, profile["include"], profile.get("exclude", [])
    )
    if not selected:
        raise IntakeError(f"{dataset_key}/{profile_name}: profile selected zero files")
    repo_id = str(dataset["repo_id"])
    resolved = str(metadata.get("sha", ""))
    if not resolved:
        raise IntakeError("resolved revision is empty")
    for row in selected:
        row["download_url"] = (
            f"{HF_BASE_URL}/datasets/{quote(repo_id, safe='/')}/resolve/"
            f"{quote(resolved, safe='')}/{quote(row['repo_path'], safe='/')}?download=true"
        )
    card = metadata.get("cardData") or {}
    basis = {
        "contract_version": CONTRACT_VERSION,
        "dataset_key": dataset_key,
        "repo_type": dataset["repo_type"],
        "repo_id": repo_id,
        "profile_name": profile_name,
        "requested_revision": dataset["revision"],
        "resolved_revision": resolved,
        "declared_license_config": dataset.get("declared_license"),
        "declared_license_remote": card.get("license"),
        "rights_status": dataset["rights_status"],
        "causal_status": dataset["causal_status"],
        "model_visibility": MODEL_VISIBILITY,
        "expected_columns": dataset["expected_columns"],
        "files": selected,
    }
    manifest_hash = sha256_bytes(canonical_json_bytes(basis))
    basis["manifest_sha256"] = manifest_hash
    basis["snapshot_id"] = f"snapshot_{manifest_hash[:24]}"
    basis["selected_file_count"] = len(selected)
    basis["selected_bytes"] = sum(row["size_bytes"] for row in selected)
    basis["training_authorized"] = False
    return basis


def _snapshot_root(
    project_root: Path, config: Mapping[str, Any], manifest: Mapping[str, Any]
) -> Path:
    raw_root = resolve_project_path(project_root, config["paths"]["raw_root"])
    path = raw_root / str(manifest["dataset_key"]) / str(manifest["snapshot_id"])
    return validate_write_path(project_root, config, path)


def _object_root(
    project_root: Path, config: Mapping[str, Any], manifest: Mapping[str, Any]
) -> Path:
    raw_root = resolve_project_path(project_root, config["paths"]["raw_root"])
    path = (
        raw_root
        / "objects"
        / str(manifest["dataset_key"])
        / str(manifest["resolved_revision"])
    )
    return validate_write_path(project_root, config, path)


def _local_file_path(
    project_root: Path,
    config: Mapping[str, Any],
    manifest: Mapping[str, Any],
    repo_path: str,
) -> Path:
    object_root = _object_root(project_root, config, manifest)
    candidate = (object_root / "files" / repo_path).resolve(strict=False)
    files_root = (object_root / "files").resolve(strict=False)
    if not _is_relative_to(candidate, files_root):
        raise IntakeError(f"unsafe repository path: {repo_path}")
    return candidate


def persist_manifest(
    project_root: Path,
    config: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> dict[str, Any]:
    initialize_catalog(project_root, config)
    snapshot_root = _snapshot_root(project_root, config, manifest)
    manifest_path = snapshot_root / "manifest.json"
    validate_write_path(project_root, config, manifest_path)
    persisted_hash = write_immutable_json(manifest_path, manifest)
    now = utc_now()
    catalog = _catalog_path(project_root, config)
    with closing(sqlite3.connect(catalog)) as conn, conn:
        conn.execute(
            """
            INSERT INTO dataset_snapshots(
              snapshot_id,dataset_key,profile_name,requested_revision,
              resolved_revision,manifest_sha256,selected_file_count,
              selected_bytes,manifest_path,first_registered_at_utc,last_verified_at_utc
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
            ON CONFLICT(snapshot_id) DO UPDATE SET
              last_verified_at_utc=excluded.last_verified_at_utc
            """,
            (
                manifest["snapshot_id"],
                manifest["dataset_key"],
                manifest["profile_name"],
                manifest["requested_revision"],
                manifest["resolved_revision"],
                manifest["manifest_sha256"],
                manifest["selected_file_count"],
                manifest["selected_bytes"],
                str(manifest_path),
                now,
                now,
            ),
        )
        for row in manifest["files"]:
            local_path = _local_file_path(
                project_root, config, manifest, row["repo_path"]
            )
            status = "COMPLETE" if (
                local_path.exists() and local_path.stat().st_size == row["size_bytes"]
            ) else "PENDING"
            conn.execute(
                """
                INSERT INTO snapshot_files(
                  snapshot_id,repo_path,size_bytes,oid,lfs_sha256,xet_hash,
                  download_url,local_path,status,local_size_bytes
                ) VALUES (?,?,?,?,?,?,?,?,?,?)
                ON CONFLICT(snapshot_id,repo_path) DO UPDATE SET
                  size_bytes=excluded.size_bytes,
                  oid=excluded.oid,
                  lfs_sha256=excluded.lfs_sha256,
                  xet_hash=excluded.xet_hash,
                  download_url=excluded.download_url,
                  local_path=excluded.local_path
                """,
                (
                    manifest["snapshot_id"],
                    row["repo_path"],
                    row["size_bytes"],
                    row.get("oid"),
                    row.get("lfs_sha256"),
                    row.get("xet_hash"),
                    row["download_url"],
                    str(local_path),
                    status,
                    local_path.stat().st_size if local_path.exists() else None,
                ),
            )
    return {
        "status": "PASS_MANIFEST_FROZEN",
        "snapshot_id": manifest["snapshot_id"],
        "manifest_path": str(manifest_path),
        "manifest_file_sha256": persisted_hash,
        "selected_file_count": manifest["selected_file_count"],
        "selected_bytes": manifest["selected_bytes"],
        "training_authorized": False,
    }


def freeze_remote_manifest(
    project_root: Path,
    config: Mapping[str, Any],
    dataset_key: str,
    profile_name: str,
    timeout: int = 60,
) -> dict[str, Any]:
    dataset = config["datasets"].get(dataset_key)
    if dataset is None:
        raise IntakeError(f"unknown dataset: {dataset_key}")
    profile = dataset["profiles"].get(profile_name)
    if profile is None:
        raise IntakeError(f"unknown profile {dataset_key}/{profile_name}")
    metadata, entries = fetch_hf_metadata_and_tree(dataset, timeout=timeout)
    manifest = construct_manifest(
        dataset_key, profile_name, dataset, profile, metadata, entries
    )
    remaining, _ = _remaining_download_bytes(
        project_root, config, manifest, max_files=None
    )
    gate = storage_gate(project_root, config, remaining)
    if not gate["pass"]:
        raise IntakeError(f"manifest exceeds storage gate: {gate}")
    result = persist_manifest(project_root, config, manifest)
    result["storage"] = gate
    result["resolved_revision"] = manifest["resolved_revision"]
    result["declared_license_remote"] = manifest["declared_license_remote"]
    result["rights_status"] = manifest["rights_status"]
    return result


def latest_manifest(
    project_root: Path,
    config: Mapping[str, Any],
    dataset_key: str,
    profile_name: str,
) -> dict[str, Any]:
    catalog = _catalog_path(project_root, config)
    if not catalog.exists():
        raise IntakeError("catalog is missing; run --stage init and manifest first")
    with closing(sqlite3.connect(catalog)) as conn:
        row = conn.execute(
            """
            SELECT manifest_path FROM dataset_snapshots
            WHERE dataset_key=? AND profile_name=?
            ORDER BY last_verified_at_utc DESC, snapshot_id DESC LIMIT 1
            """,
            (dataset_key, profile_name),
        ).fetchone()
    if not row:
        raise IntakeError(f"no frozen manifest for {dataset_key}/{profile_name}")
    path = Path(row[0])
    manifest = read_json(path)
    expected = manifest.get("manifest_sha256")
    basis = dict(manifest)
    for key in (
        "manifest_sha256",
        "snapshot_id",
        "selected_file_count",
        "selected_bytes",
        "training_authorized",
    ):
        basis.pop(key, None)
    observed = sha256_bytes(canonical_json_bytes(basis))
    if observed != expected:
        raise IntakeError(f"frozen manifest hash mismatch: {path}")
    return manifest


class _ResponseProtocol:
    status: int
    headers: Mapping[str, str]

    def read(self, size: int = -1) -> bytes:  # pragma: no cover - protocol only
        raise NotImplementedError

    def __enter__(self):  # pragma: no cover - protocol only
        return self

    def __exit__(self, *args):  # pragma: no cover - protocol only
        return None


def stream_download(
    url: str,
    destination: Path,
    expected_size: int,
    expected_sha256: str | None,
    token: str | None,
    chunk_bytes: int,
    progress_every_bytes: int,
    timeout: int = 120,
    opener: Callable[[str, Mapping[str, str], int], Any] = _open_url,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    destination.parent.mkdir(parents=True, exist_ok=True)
    if destination.exists():
        size = destination.stat().st_size
        if size != expected_size:
            raise IntakeError(
                f"completed file has unexpected size {size} != {expected_size}: {destination}"
            )
        digest = sha256_file(destination) if expected_sha256 else None
        if expected_sha256 and digest != expected_sha256:
            raise IntakeError(f"completed file checksum mismatch: {destination}")
        return {
            "status": "ALREADY_COMPLETE",
            "bytes_transferred": 0,
            "local_size_bytes": size,
            "local_sha256": digest,
            "resumed_from_bytes": size,
        }

    partial = destination.with_name(destination.name + ".part")
    resume_from = partial.stat().st_size if partial.exists() else 0
    if resume_from > expected_size:
        raise IntakeError(f"partial file is larger than expected: {partial}")
    headers = _headers(token, {"Accept": "application/octet-stream"})
    if resume_from:
        headers["Range"] = f"bytes={resume_from}-"
    response = opener(url, headers, timeout)
    transferred = 0
    mode = "ab" if resume_from else "wb"
    with response:
        status = int(getattr(response, "status", 200))
        if resume_from and status != 206:
            raise IntakeError(
                "remote endpoint did not honor Range; partial file was preserved for review"
            )
        if not resume_from and status not in (200, 206):
            raise IntakeError(f"unexpected download HTTP status {status}")
        next_progress = max(progress_every_bytes, resume_from + progress_every_bytes)
        with partial.open(mode) as handle:
            while True:
                chunk = response.read(chunk_bytes)
                if not chunk:
                    break
                handle.write(chunk)
                transferred += len(chunk)
                current = resume_from + transferred
                if progress and current >= next_progress:
                    progress(
                        {
                            "status": "DOWNLOADING",
                            "path": str(destination),
                            "downloaded_bytes": current,
                            "expected_bytes": expected_size,
                        }
                    )
                    next_progress = current + progress_every_bytes
            handle.flush()
            os.fsync(handle.fileno())
    final_size = partial.stat().st_size
    if final_size != expected_size:
        raise IntakeError(
            f"download ended at {final_size} bytes, expected {expected_size}; partial retained"
        )
    digest = sha256_file(partial)
    if expected_sha256 and digest != expected_sha256:
        raise IntakeError("download checksum mismatch; partial retained")
    os.replace(partial, destination)
    return {
        "status": "COMPLETE",
        "bytes_transferred": transferred,
        "local_size_bytes": final_size,
        "local_sha256": digest,
        "resumed_from_bytes": resume_from,
    }


@contextmanager
def _exclusive_lock(path: Path) -> Iterator[None]:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle = path.open("a+b")
    try:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError as exc:
            raise IntakeError(f"another intake process holds {path}") from exc
        yield
    finally:
        try:
            import fcntl

            fcntl.flock(handle.fileno(), fcntl.LOCK_UN)
        finally:
            handle.close()


def _remaining_download_bytes(
    project_root: Path,
    config: Mapping[str, Any],
    manifest: Mapping[str, Any],
    max_files: int | None,
) -> tuple[int, list[dict[str, Any]]]:
    rows = list(manifest["files"])
    if max_files is not None:
        rows = rows[: max(0, max_files)]
    remaining = 0
    for row in rows:
        final = _local_file_path(project_root, config, manifest, row["repo_path"])
        partial = final.with_name(final.name + ".part")
        if final.exists() and final.stat().st_size == row["size_bytes"]:
            continue
        partial_size = partial.stat().st_size if partial.exists() else 0
        remaining += max(0, int(row["size_bytes"]) - partial_size)
    return remaining, rows


def download_snapshot(
    project_root: Path,
    config: Mapping[str, Any],
    dataset_key: str,
    profile_name: str,
    *,
    max_files: int | None = None,
    dry_run: bool = False,
    progress: Callable[[dict[str, Any]], None] | None = None,
) -> dict[str, Any]:
    manifest = latest_manifest(project_root, config, dataset_key, profile_name)
    dataset = config["datasets"][dataset_key]
    token = _token(dataset)
    object_root = _object_root(project_root, config, manifest)
    remaining, rows = _remaining_download_bytes(
        project_root, config, manifest, max_files
    )
    gate = storage_gate(project_root, config, remaining)
    if not gate["pass"]:
        raise IntakeError(f"download blocked by storage gate: {gate}")
    if dry_run:
        return {
            "status": "PASS_DOWNLOAD_DRY_RUN",
            "snapshot_id": manifest["snapshot_id"],
            "files_considered": len(rows),
            "remaining_bytes": remaining,
            "storage": gate,
            "preallocation_performed": False,
        }

    catalog = _catalog_path(project_root, config)
    run_id = f"run_{uuid.uuid4().hex}"
    started = utc_now()
    with closing(sqlite3.connect(catalog)) as conn, conn:
        conn.execute(
            "INSERT INTO intake_runs(run_id,stage,dataset_key,profile_name,snapshot_id,started_at_utc,status,planned_bytes) VALUES (?,?,?,?,?,?,?,?)",
            (
                run_id,
                "download",
                dataset_key,
                profile_name,
                manifest["snapshot_id"],
                started,
                "RUNNING",
                remaining,
            ),
        )
    completed = 0
    skipped = 0
    transferred = 0
    failures: dict[str, str] = {}
    lock_path = object_root / ".download.lock"
    with _exclusive_lock(lock_path):
        for row in rows:
            local = validate_write_path(
                project_root,
                config,
                _local_file_path(project_root, config, manifest, row["repo_path"]),
            )
            try:
                result = stream_download(
                    row["download_url"],
                    local,
                    int(row["size_bytes"]),
                    row.get("lfs_sha256"),
                    token,
                    int(config["storage"]["download_chunk_bytes"]),
                    int(config["storage"]["progress_every_bytes"]),
                    progress=progress,
                )
                transferred += int(result["bytes_transferred"])
                if result["status"] == "ALREADY_COMPLETE":
                    skipped += 1
                else:
                    completed += 1
                with closing(sqlite3.connect(catalog)) as conn, conn:
                    conn.execute(
                        """
                        UPDATE snapshot_files SET status='COMPLETE',local_size_bytes=?,
                        local_sha256=?,completed_at_utc=?,last_error=NULL
                        WHERE snapshot_id=? AND repo_path=?
                        """,
                        (
                            result["local_size_bytes"],
                            result["local_sha256"],
                            utc_now(),
                            manifest["snapshot_id"],
                            row["repo_path"],
                        ),
                    )
            except Exception as exc:
                failures[row["repo_path"]] = str(exc)
                with closing(sqlite3.connect(catalog)) as conn, conn:
                    conn.execute(
                        "UPDATE snapshot_files SET status='FAILED',last_error=? WHERE snapshot_id=? AND repo_path=?",
                        (str(exc), manifest["snapshot_id"], row["repo_path"]),
                    )
                break

    status = "PASS_DOWNLOAD_COMPLETE" if not failures else "INCOMPLETE_DOWNLOAD_REVIEW"
    result = {
        "contract_version": CONTRACT_VERSION,
        "status": status,
        "run_id": run_id,
        "snapshot_id": manifest["snapshot_id"],
        "dataset_key": dataset_key,
        "profile_name": profile_name,
        "files_considered": len(rows),
        "files_completed_this_run": completed,
        "files_already_complete": skipped,
        "bytes_transferred_this_run": transferred,
        "failures": failures,
        "storage_before": gate,
        "managed_usage_after_bytes": managed_storage_usage(project_root, config),
        "training_authorized": False,
        "feature_visibility": MODEL_VISIBILITY,
        "v009_interaction": "NONE",
    }
    path = report_path(
        project_root, config, "download", dataset_key, manifest["snapshot_id"]
    )
    report_hash = write_json(path, result)
    result["report_path"] = str(path)
    result["report_sha256"] = report_hash
    with closing(sqlite3.connect(catalog)) as conn, conn:
        conn.execute(
            """
            UPDATE intake_runs SET finished_at_utc=?,status=?,transferred_bytes=?,
            report_path=?,error_json=? WHERE run_id=?
            """,
            (
                utc_now(),
                status,
                transferred,
                str(path),
                json.dumps(failures, sort_keys=True) if failures else None,
                run_id,
            ),
        )
    return result


def _duckdb_module():
    try:
        import duckdb
    except ImportError as exc:
        raise IntakeError(
            "DuckDB is required for Parquet schema/content audit; install the separate intake requirements"
        ) from exc
    return duckdb


def _sql_string(value: str | Path) -> str:
    return "'" + str(value).replace("'", "''") + "'"


def _parquet_source_sql(paths: list[Path]) -> str:
    if not paths:
        raise IntakeError("no complete Parquet files are available")
    values = ",".join(_sql_string(path) for path in paths)
    return f"read_parquet([{values}], union_by_name=true, filename=true)"


def _complete_files(
    project_root: Path,
    config: Mapping[str, Any],
    manifest: Mapping[str, Any],
) -> tuple[list[Path], list[dict[str, Any]]]:
    complete: list[Path] = []
    checks: list[dict[str, Any]] = []
    for row in manifest["files"]:
        path = _local_file_path(project_root, config, manifest, row["repo_path"])
        exists = path.exists()
        size = path.stat().st_size if exists else None
        good = exists and size == row["size_bytes"]
        checks.append(
            {
                "repo_path": row["repo_path"],
                "local_path": str(path),
                "exists": exists,
                "expected_size_bytes": row["size_bytes"],
                "local_size_bytes": size,
                "size_matches": good,
            }
        )
        if good:
            complete.append(path)
    return complete, checks


def audit_snapshot(
    project_root: Path,
    config: Mapping[str, Any],
    dataset_key: str,
    profile_name: str,
    *,
    level: str = "metadata",
) -> dict[str, Any]:
    if level not in ("integrity", "metadata", "full"):
        raise IntakeError("audit level must be integrity, metadata or full")
    manifest = latest_manifest(project_root, config, dataset_key, profile_name)
    complete, checks = _complete_files(project_root, config, manifest)
    all_complete = len(complete) == len(checks)
    result: dict[str, Any] = {
        "contract_version": CONTRACT_VERSION,
        "snapshot_id": manifest["snapshot_id"],
        "dataset_key": dataset_key,
        "profile_name": profile_name,
        "audit_level": level,
        "file_integrity": {
            "selected_files": len(checks),
            "complete_files": len(complete),
            "all_files_complete": all_complete,
            "files": checks,
        },
        "rights_status": manifest["rights_status"],
        "causal_status": manifest["causal_status"],
        "training_authorized": False,
        "feature_visibility": MODEL_VISIBILITY,
        "v009_interaction": "NONE",
    }
    status = "PASS_RAW_FILE_INTEGRITY" if all_complete else "INCOMPLETE_RAW_FILES"
    if level != "integrity" and all_complete:
        duckdb = _duckdb_module()
        source = _parquet_source_sql(complete)
        con = duckdb.connect(database=":memory:")
        try:
            description = con.execute(f"DESCRIBE SELECT * FROM {source}").fetchall()
            columns = [str(row[0]) for row in description]
            expected = list(config["datasets"][dataset_key]["expected_columns"])
            missing = sorted(set(expected) - set(columns))
            row_count = int(con.execute(f"SELECT COUNT(*) FROM {source}").fetchone()[0])
            result["parquet"] = {
                "duckdb_version": getattr(duckdb, "__version__", "unknown"),
                "columns": columns,
                "expected_columns": expected,
                "missing_expected_columns": missing,
                "row_count": row_count,
            }
            data_kind = config["datasets"][dataset_key].get("data_kind")
            if missing:
                status = "REVIEW_SCHEMA_MISMATCH"
            elif data_kind == "bars_1m" and level == "full":
                metrics = con.execute(
                    f"""
                    SELECT
                      MIN(timestamp), MAX(timestamp), COUNT(DISTINCT ticker),
                      SUM(CASE WHEN open IS NULL OR high IS NULL OR low IS NULL OR close IS NULL THEN 1 ELSE 0 END),
                      SUM(CASE WHEN open<=0 OR high<=0 OR low<=0 OR close<=0 THEN 1 ELSE 0 END),
                      SUM(CASE WHEN high<GREATEST(open,close,low) OR low>LEAST(open,close,high) THEN 1 ELSE 0 END),
                      SUM(CASE WHEN volume IS NULL OR volume<0 THEN 1 ELSE 0 END),
                      SUM(CASE WHEN trade_count IS NULL OR trade_count<0 THEN 1 ELSE 0 END)
                    FROM {source}
                    """
                ).fetchone()
                result["bars_content"] = {
                    "min_timestamp": str(metrics[0]),
                    "max_timestamp": str(metrics[1]),
                    "distinct_tickers": int(metrics[2]),
                    "null_ohlc_rows": int(metrics[3]),
                    "nonpositive_ohlc_rows": int(metrics[4]),
                    "ohlc_envelope_violations": int(metrics[5]),
                    "invalid_volume_rows": int(metrics[6]),
                    "invalid_trade_count_rows": int(metrics[7]),
                    "feed_identity": "UNKNOWN_REQUIRES_PROVENANCE_REVIEW",
                    "canonical_status": "BLOCKED_PENDING_OPENING_AND_CROSS_SOURCE_AUDIT",
                }
                hard_bad = sum(int(metrics[index]) for index in range(3, 8))
                status = (
                    "PASS_BARS_STRUCTURAL_CONTENT_AUDIT"
                    if hard_bad == 0
                    else "REVIEW_BARS_CONTENT_ANOMALIES"
                )
            elif data_kind == "news_documents" and level == "full":
                metrics = con.execute(
                    f"""
                    SELECT
                      MIN(date), MAX(date),
                      SUM(CASE WHEN date IS NULL OR TRIM(CAST(date AS VARCHAR))='' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN text IS NULL OR TRIM(CAST(text AS VARCHAR))='' THEN 1 ELSE 0 END),
                      SUM(CASE WHEN extra_fields IS NULL OR TRIM(CAST(extra_fields AS VARCHAR))='' THEN 1 ELSE 0 END),
                      APPROX_COUNT_DISTINCT(hash(date,text))
                    FROM {source}
                    """
                ).fetchone()
                result["news_content"] = {
                    "min_date": str(metrics[0]),
                    "max_date": str(metrics[1]),
                    "missing_date_rows": int(metrics[2]),
                    "missing_text_rows": int(metrics[3]),
                    "missing_extra_fields_rows": int(metrics[4]),
                    "approx_distinct_date_text": int(metrics[5]),
                    "causal_eligibility": "BLOCKED_PENDING_ROW_LEVEL_TIME_PRECISION_AND_AVAILABLE_AT_AUDIT",
                    "redistribution": "FORBIDDEN_BY_PROJECT_POLICY",
                }
                status = "REVIEW_NEWS_RIGHTS_TIME_AND_DEDUP_REQUIRED"
            elif not missing:
                status = "PASS_RAW_DOWNLOAD_SCHEMA_REVIEW_READY"
        finally:
            con.close()
    result["status"] = status
    path = report_path(
        project_root, config, f"audit_{level}", dataset_key, manifest["snapshot_id"]
    )
    report_hash = write_json(path, result)
    result["report_path"] = str(path)
    result["report_sha256"] = report_hash
    initialize_catalog(project_root, config)
    audit_id = f"audit_{uuid.uuid4().hex}"
    with closing(sqlite3.connect(_catalog_path(project_root, config))) as conn, conn:
        conn.execute(
            "INSERT INTO audit_records VALUES (?,?,?,?,?,?,?)",
            (
                audit_id,
                manifest["snapshot_id"],
                level,
                status,
                utc_now(),
                str(path),
                report_hash,
            ),
        )
    return result


def sample_snapshot(
    project_root: Path,
    config: Mapping[str, Any],
    sample_kind: str,
) -> dict[str, Any]:
    if sample_kind not in ("bars", "news"):
        raise IntakeError("sample kind must be bars or news")
    sample = config["samples"][sample_kind]
    dataset_key = sample["dataset_key"]
    profile = sample["profile"]
    manifest = latest_manifest(project_root, config, dataset_key, profile)
    complete, checks = _complete_files(project_root, config, manifest)
    if len(complete) != len(checks):
        raise IntakeError("all selected files must be complete before sampling")
    duckdb = _duckdb_module()
    source = _parquet_source_sql(complete)
    lake_root = resolve_project_path(project_root, config["paths"]["lake_root"])
    output = validate_write_path(
        project_root,
        config,
        lake_root / sample_kind / manifest["snapshot_id"] / "sample.parquet",
    )
    output.parent.mkdir(parents=True, exist_ok=True)
    con = duckdb.connect(database=":memory:")
    try:
        if sample_kind == "bars":
            tickers = ",".join(_sql_string(item) for item in sample["tickers"])
            windows = " OR ".join(
                "(timestamp>=CAST({} AS TIMESTAMPTZ) AND timestamp<CAST({} AS TIMESTAMPTZ))".format(
                    _sql_string(start), _sql_string(end)
                )
                for start, end in sample["windows_utc"]
            )
            query = (
                f"SELECT * FROM {source} WHERE ticker IN ({tickers}) AND ({windows}) "
                "ORDER BY ticker,timestamp"
            )
        else:
            rows = int(sample["rows"])
            seed = int(sample["seed"])
            query = (
                f"SELECT * FROM {source} USING SAMPLE reservoir({rows} ROWS) "
                f"REPEATABLE ({seed})"
            )
        con.execute(
            f"COPY ({query}) TO {_sql_string(output)} (FORMAT PARQUET, COMPRESSION ZSTD)"
        )
        count = int(con.execute(f"SELECT COUNT(*) FROM read_parquet({_sql_string(output)})").fetchone()[0])
        if sample_kind == "bars":
            metrics = con.execute(
                f"""
                SELECT COUNT(DISTINCT ticker),MIN(timestamp),MAX(timestamp),
                SUM(CASE WHEN high<GREATEST(open,close,low) OR low>LEAST(open,close,high) THEN 1 ELSE 0 END),
                COUNT(*)-COUNT(DISTINCT ticker||'|'||CAST(timestamp AS VARCHAR))
                FROM read_parquet({_sql_string(output)})
                """
            ).fetchone()
            sample_metrics = {
                "rows": count,
                "tickers": int(metrics[0]),
                "min_timestamp": str(metrics[1]),
                "max_timestamp": str(metrics[2]),
                "ohlc_envelope_violations": int(metrics[3]),
                "duplicate_ticker_timestamp_rows": int(metrics[4]),
            }
        else:
            sample_metrics = {"rows": count}
    finally:
        con.close()
    result = {
        "contract_version": CONTRACT_VERSION,
        "status": "PASS_STRUCTURAL_SAMPLE_CREATED",
        "sample_kind": sample_kind,
        "snapshot_id": manifest["snapshot_id"],
        "output_path": str(output),
        "output_size_bytes": output.stat().st_size,
        "output_sha256": sha256_file(output),
        "metrics": sample_metrics,
        "interpretation": "Structural research sample only; not canonical, point-in-time, model-visible or a training authorization.",
        "training_authorized": False,
        "v009_interaction": "NONE",
    }
    path = report_path(
        project_root, config, "sample", dataset_key, manifest["snapshot_id"]
    )
    result["report_sha256"] = write_json(path, result)
    result["report_path"] = str(path)
    return result

"""Bounded, read-only project evidence inventory. See docs/CONTEXT_RECOVERY.md.

Standard library only. Does not import pipelines, deserialize models or run experiments.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from datetime import datetime, timezone
import hashlib
import json
import math
import os
from pathlib import Path
import re
import sqlite3
import subprocess
import sys
import tempfile
import time

VERSION = "project_context_v001"
OUTPUT = "reports/project_context/latest"
CANONICAL = [
    "ARCHITECTURE.md", "ARCHITECTURE_EVENT_LAYER.md", "docs/RESEARCH_STATUS.md",
    "docs/RESEARCH_DECISIONS.md", "docs/ROADMAP.md", "docs/DATA_CONTRACTS.md",
    "docs/EXPERIMENTS.md", "AGENTS.md", "docs/INDEX.md", "docs/DOCUMENTATION_POLICY.md",
]
SKIP_DIRS = {".git", ".venv", "venv", "env", "node_modules", ".tox"}
CACHE_DIRS = {"__pycache__", ".pytest_cache", ".mypy_cache", ".ruff_cache"}
DB_SUFFIXES = {".db", ".sqlite", ".sqlite3"}
TEXT_SUFFIXES = {".md", ".py", ".sql", ".json", ".toml", ".yaml", ".yml", ".txt"}
GROUP_FIELDS = {
    "source", "source_name", "source_provider", "provider", "publisher_name", "source_type",
    "interval", "label_status", "horizon_sessions", "feature_version", "label_version",
    "model_version", "normalizer_version", "normalization_version", "strict_pit",
    "availability_is_point_in_time", "state_point_in_time_verified",
    "point_in_time_evidence_fraction", "event_type", "form", "status",
}
TIME_FIELDS = {
    "timestamp", "trading_day", "origin_trading_day", "target_trading_day", "state_time",
    "published_at", "ingested_at", "retrieved_at", "observed_at", "available_at",
    "first_seen_at", "accepted_at", "sealed_at_utc", "fitted_at_utc",
}
ASSET_FIELDS = {"asset_id", "asset_ticker", "ticker", "symbol"}
REFERENCE = re.compile(
    r"(?<![\w/])((?:database|config|data|reports|models|docs|ingestion|pipeline|research|tools|tests)"
    r"/[A-Za-z0-9_./{}*+-]+\.(?:sql|json|db|sqlite3?|csv|joblib|pkl|md|py|jsonl|txt))"
)
MAX_TEXT_BYTES = 2 * 1024 * 1024
HASH_BUDGET_BYTES = 128 * 1024 * 1024


def utc_now():
    return datetime.now(timezone.utc).isoformat()


def json_text(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, indent=2, allow_nan=False) + "\n"


def sha_bytes(value):
    return hashlib.sha256(value).hexdigest()


def sha_file(path):
    h = hashlib.sha256()
    with path.open("rb") as stream:
        for block in iter(lambda: stream.read(1024 * 1024), b""):
            h.update(block)
    return h.hexdigest()


def safe_path(root, relative):
    """Reject traversal and ANY symlink, including ancestors."""
    rel = Path(relative)
    if rel.is_absolute() or ".." in rel.parts or not rel.parts:
        raise ValueError("path must be relative and inside the repository")
    path = root
    for part in rel.parts:
        path = path / part
        if path.is_symlink():
            raise ValueError("symlink path is outside the inspection contract")
    if not path.resolve().is_relative_to(root.resolve()):
        raise ValueError("path escapes repository")
    return path


def secret_path(relative):
    p = Path(relative)
    return (
        p.name in {".env", "credentials.json", "secrets.json"} or p.name.startswith(".env.")
        or p.name.endswith(".local.json") or p.suffix.lower() in {".pem", ".key"}
        or any(part.lower() in {"secrets", ".ssh", ".aws"} for part in p.parts)
    )


def redact(value):
    """Best-effort minimization; NOT a certification that output is public."""
    if isinstance(value, dict):
        return {k: redact(v) for k, v in value.items()}
    if isinstance(value, list):
        return [redact(v) for v in value]
    if not isinstance(value, str):
        return value
    value = re.sub(r"[\w.+-]+@[\w.-]+\.[A-Za-z]{2,}", "[email-redacted]", value)
    value = re.sub(r"(https?://)[^/\s@]+@", r"\1[credentials-redacted]@", value)
    value = re.sub(r"(https?://[^\s?\"<>]+)\?[^\s\"<>]+", r"\1?[query-redacted]", value)
    return re.sub(r"(?i)((?:api[_-]?key|token|password|secret)\s*[=:]\s*)[^\s,;\"}]+",
                  r"\1[redacted]", value)


def category(relative):
    p = Path(relative)
    if secret_path(relative):
        return "PRIVATE_CONFIGURATION"
    if any(part in CACHE_DIRS for part in p.parts) or p.suffix in {".pyc", ".pyo"}:
        return "REGENERABLE_CACHE"
    if p.name.endswith(":Zone.Identifier"):
        return "DOWNLOAD_METADATA"
    if p.suffix.lower() in DB_SUFFIXES or re.search(r"\.(?:db|sqlite3?)-(?:wal|shm|journal)$", relative):
        return "DATABASE_OR_SIDECAR_PROTECTED"
    if relative.startswith("data/raw/"):
        return "RAW_EVIDENCE_PROTECTED"
    if p.suffix in {".pkl", ".joblib", ".npz", ".npy", ".parquet"} or "/artifacts/" in relative:
        return "MODEL_OR_DATA_ARTIFACT_PROTECTED"
    if relative.startswith(("reports/", "data/processed/")):
        return "RESEARCH_EVIDENCE_PROTECTED"
    if relative.startswith(("docs/archive/", "docs/package-notes/")):
        return "HISTORICAL_DOCUMENTATION"
    if p.suffix in {".zip", ".tar", ".gz", ".7z"}:
        return "PACKAGE_REVIEW"
    if len(p.parts) == 1 and ("INSTALL" in p.name or p.name.startswith("README_")):
        return "ROOT_NOTE_REVIEW"
    return "SOURCE_OR_DOCUMENTATION"


def inventory(root):
    files, skipped, errors = {}, [], []
    for current, dirs, names in os.walk(root, followlinks=False, onerror=lambda e: errors.append(str(e))):
        here = Path(current)
        kept = []
        for name in sorted(dirs):
            path = here / name
            rel = path.relative_to(root).as_posix()
            if name in SKIP_DIRS or rel == "reports/project_context" or path.is_symlink():
                skipped.append(rel)
            else:
                kept.append(name)
        dirs[:] = kept
        for name in sorted(names):
            path = here / name
            rel = path.relative_to(root).as_posix()
            try:
                if path.is_symlink():
                    files[rel] = {"kind": "SYMLINK_NOT_FOLLOWED"}
                    continue
                stat = path.stat()
                files[rel] = {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns, "kind": category(rel)}
            except OSError as exc:
                errors.append(f"{rel}: {exc}")
    return files, {"excluded_directories": skipped, "errors": errors}


def run_git(root, *args, input_text=None, timeout=15):
    try:
        proc = subprocess.run(
            ["git", "-C", str(root), *args], input=input_text,
            stdout=subprocess.PIPE, stderr=subprocess.PIPE, text=True,
            encoding="utf-8", errors="replace", timeout=timeout, check=False,
            env={**os.environ, "GIT_OPTIONAL_LOCKS": "0", "GIT_TERMINAL_PROMPT": "0"},
        )
        return proc.returncode, proc.stdout, proc.stderr
    except (OSError, subprocess.TimeoutExpired) as exc:
        return -1, "", str(exc)


def git_inventory(root, files, remote=False):
    rc, head, err = run_git(root, "rev-parse", "HEAD")
    if rc:
        for item in files.values():
            item["git"] = "UNKNOWN_NO_GIT"
        return {"status": "UNAVAILABLE", "error": err, "live_remote": {"status": "NOT_CHECKED"}}
    responses = [
        run_git(root, "ls-files", "-z"),
        run_git(root, "ls-files", "--others", "--ignored", "--exclude-standard", "-z"),
        run_git(root, "ls-files", "--others", "--exclude-standard", "-z"),
    ]
    tracked, ignored, untracked = [set(r[1].split("\0")) - {""} for r in responses]
    for path, item in files.items():
        item["git"] = ("TRACKED" if path in tracked else "IGNORED" if path in ignored
                       else "UNTRACKED" if path in untracked else "UNKNOWN")
    status_rc, porcelain, status_err = run_git(root, "status", "--porcelain=v1", "-z", "--untracked-files=all")
    changes = [s for s in porcelain.split("\0") if s and "reports/project_context/" not in s]
    _, branch, _ = run_git(root, "branch", "--show-current")
    _, origin, _ = run_git(root, "remote", "get-url", "origin")
    cached_rc, cached, _ = run_git(root, "rev-parse", "--verify", "refs/remotes/origin/main")
    result = {
        "status": "OK" if not status_rc and all(r[0] == 0 for r in responses) else "PARTIAL",
        "errors": [r[2] for r in responses if r[2]] + ([status_err] if status_err else []),
        "head": head.strip(), "branch": branch.strip(), "origin": redact(origin.strip()),
        "worktree_changes": changes,
        "cached_origin_main": cached.strip() if not cached_rc else None,
        "cached_ref_is_not_live_remote": True,
        "tracked_missing_locally": sorted(tracked - files.keys()),
        "live_remote": {"status": "NOT_CHECKED", "reason": "offline by default; use --remote"},
    }
    if remote:
        code, out, error = run_git(root, "ls-remote", "origin", "refs/heads/main", timeout=20)
        tip = out.split()[0] if not code and out.strip() else None
        result["live_remote"] = {
            "status": "OBSERVED" if tip else "UNAVAILABLE", "observed_at_utc": utc_now(),
            "main_sha": tip, "equals_local_head": tip == head.strip() if tip else None,
            "error": redact(error) if error else None,
        }
    return result


def hash_and_references(root, files):
    references, texts, errors, used = defaultdict(set), {}, [], 0
    for rel, item in sorted(files.items()):
        if (item["kind"] not in {"SOURCE_OR_DOCUMENTATION", "HISTORICAL_DOCUMENTATION",
                                 "ROOT_NOTE_REVIEW", "RESEARCH_EVIDENCE_PROTECTED"}
                or Path(rel).suffix not in TEXT_SUFFIXES):
            continue
        if item.get("bytes", 0) > MAX_TEXT_BYTES or used + item.get("bytes", 0) > HASH_BUDGET_BYTES:
            item["content_inspection"] = "SKIPPED_SIZE_BUDGET"
            continue
        try:
            data = safe_path(root, rel).read_bytes()
            used += len(data)
            item["sha256"] = sha_bytes(data)
            content = data.decode("utf-8")
            for match in REFERENCE.finditer(content):
                references[match.group(1)].add(rel)
            if Path(rel).suffix in {".md", ".json"}:
                texts[rel] = content
        except (OSError, UnicodeError, ValueError) as exc:
            errors.append(f"{rel}: {exc}")
    return texts, {key: sorted(value) for key, value in sorted(references.items())}, errors


def documentation(texts, files):
    result = []
    for path in CANONICAL:
        content = texts.get(path)
        row = {"path": path, "status": "OBSERVED" if content is not None else "MISSING_OR_UNREAD",
               "sha256": files.get(path, {}).get("sha256")}
        if content is not None:
            row["headings"] = [
                {"line": i, "heading": line} for i, line in enumerate(content.splitlines(), 1)
                if re.match(r"^#{1,3} ", line)
            ]
            if path.endswith("ROADMAP.md"):
                match = re.search(r"^## Active sequence.*?(?=^## Phase|\Z)", content, re.M | re.S)
                row["active_sequence_excerpt"] = match.group(0) if match else None
        result.append(row)
    return result


def quote(identifier):
    return '"' + identifier.replace('"', '""') + '"'


def db_stamp(path):
    out = {}
    for suffix in ("", "-wal", "-journal"):
        part = Path(str(path) + suffix)
        try:
            stat = part.stat()
            out[suffix or "db"] = {"bytes": stat.st_size, "mtime_ns": stat.st_mtime_ns}
        except FileNotFoundError:
            out[suffix or "db"] = None
    return out


class Reader:
    def __init__(self, path, query_seconds, database_seconds):
        # URI escaping matters for spaces/#/?. Never immutable=1: it would ignore WAL.
        self.conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=0.1)
        self.conn.row_factory = sqlite3.Row
        self.conn.execute("PRAGMA query_only=ON")
        self.conn.execute("PRAGMA trusted_schema=OFF")
        self.conn.execute("PRAGMA temp_store=MEMORY")
        self.query_seconds = query_seconds
        self.deadline = time.monotonic() + database_seconds

    def query(self, sql, params=(), limit=1000):
        if not re.match(r"^\s*(SELECT|WITH)\b", sql, re.I):
            return {"status": "ERROR", "error": "Only SELECT/WITH probes are allowed", "sql": sql}
        now = time.monotonic()
        if now >= self.deadline:
            return {"status": "SKIPPED_DATABASE_BUDGET", "sql": sql, "parameters": list(params)}
        end = min(now + self.query_seconds, self.deadline)
        self.conn.set_progress_handler(lambda: int(time.monotonic() >= end), 1000)
        allowed = {sqlite3.SQLITE_SELECT, sqlite3.SQLITE_READ, sqlite3.SQLITE_FUNCTION, sqlite3.SQLITE_RECURSIVE}
        def authorize(action, arg1, arg2, database, trigger):
            if action not in allowed or (action == sqlite3.SQLITE_FUNCTION and
                                         (arg2 or "").lower() in {"load_extension", "readfile", "writefile"}):
                return sqlite3.SQLITE_DENY
            return sqlite3.SQLITE_OK
        self.conn.set_authorizer(authorize)
        try:
            rows = self.conn.execute(sql, params).fetchmany(limit + 1)
            return {
                "status": "TRUNCATED" if len(rows) > limit else "EXACT",
                "rows": [dict(row) for row in rows[:limit]], "sql": sql, "parameters": list(params),
                "elapsed_seconds": round(time.monotonic() - now, 4),
            }
        except sqlite3.Error as exc:
            return {"status": "TIMEOUT" if "interrupted" in str(exc) else "ERROR",
                    "error": str(exc), "sql": sql, "parameters": list(params)}
        finally:
            self.conn.set_progress_handler(None, 0)
            self.conn.set_authorizer(None)


def scalar_result(result, field="n"):
    if result.get("status") == "EXACT" and len(result.get("rows", [])) == 1:
        return result["rows"][0].get(field)
    return None


def inspect_database(root, relative, spec, query_seconds, database_seconds, schema_only=False):
    result = {"path": relative, "role": spec.get("role", "UNCLASSIFIED_DATABASE_REVIEW"),
              "status": "UNAVAILABLE", "tables": {}, "probes": {}}
    try:
        path = safe_path(root, relative)
        if not path.is_file():
            result["status"] = "MISSING"
            return result
        before = db_stamp(path)
        reader = Reader(path, query_seconds, database_seconds)
    except (ValueError, OSError, sqlite3.Error) as exc:
        result["error"] = str(exc)
        return result
    try:
        conn = reader.conn
        version_before = conn.execute("PRAGMA data_version").fetchone()[0]
        objects = [dict(row) for row in conn.execute(
            "SELECT type,name,tbl_name,sql FROM sqlite_master WHERE name NOT LIKE 'sqlite_%' ORDER BY type,name"
        )]
        result.update({
            "status": "OBSERVED", "snapshot_started_at_utc": utc_now(), "file_before": before,
            "schema_sha256": sha_bytes(json_text(objects).encode()), "schema_objects": objects,
            "schema_version": conn.execute("PRAGMA schema_version").fetchone()[0],
            "user_version": conn.execute("PRAGMA user_version").fetchone()[0],
        })
        for obj in objects:
            if obj["type"] not in {"table", "view"}:
                continue
            name = obj["name"]
            result["tables"][name] = {
                "type": obj["type"], "virtual": (obj["sql"] or "").upper().startswith("CREATE VIRTUAL"),
                "columns": [dict(row) for row in conn.execute(f"PRAGMA table_xinfo({quote(name)})")],
                "foreign_keys": [dict(row) for row in conn.execute(f"PRAGMA foreign_key_list({quote(name)})")],
                "indexes": [dict(row) for row in conn.execute(f"PRAGMA index_list({quote(name)})")],
                "count": {"status": "NOT_QUERIED"},
            }
        result["migration_metadata"] = {}
        for name in ("schema_migrations", "schema_meta"):
            if name in result["tables"] and not schema_only:
                result["migration_metadata"][name] = reader.query(f"SELECT * FROM {quote(name)}", limit=200)
        for probe in spec.get("probes", []):
            result["probes"][probe["id"]] = (
                {"status": "NOT_QUERIED"} if schema_only
                else reader.query(probe["sql"], probe.get("parameters", []))
            )
        priority = list(spec.get("profile_tables", []))
        if not priority:
            # New/auxiliary DBs get conservative automatic metadata profiles.
            priority = [name for name, info in result["tables"].items()
                        if info["type"] == "table" and not info["virtual"] and
                        any(c["name"].lower() in GROUP_FIELDS - {"status"} or
                            c["name"].lower().endswith("_version") for c in info["columns"])]
        ordered = sorted(result["tables"], key=lambda t: (t not in priority, priority.index(t) if t in priority else 0, t))
        for name in ordered:
            info = result["tables"][name]
            if not schema_only and info["type"] == "table" and not info["virtual"]:
                info["count"] = reader.query(f"SELECT COUNT(*) AS n FROM {quote(name)}")
        for name in priority:
            if name not in result["tables"]:
                result.setdefault("missing_profile_tables", []).append(name)
                continue
            info = result["tables"][name]
            if schema_only or info["virtual"] or info["type"] != "table":
                continue
            names = [col["name"] for col in info["columns"]]
            info["groups"] = {}
            for field in names:
                if field.lower() in GROUP_FIELDS or field.lower().endswith("_version"):
                    info["groups"][field] = reader.query(
                        f"SELECT {quote(field)} AS value, COUNT(*) AS n FROM {quote(name)} "
                        f"GROUP BY {quote(field)} ORDER BY n DESC, value", limit=200
                    )
            expressions = []
            for field in names:
                q = quote(field)
                if field.lower() in TIME_FIELDS:
                    expressions.extend([f"MIN({q}) AS {quote(field + '__min')}",
                                        f"MAX({q}) AS {quote(field + '__max')}"])
                elif field.lower() in ASSET_FIELDS:
                    expressions.append(f"COUNT(DISTINCT {q}) AS {quote(field + '__distinct')}")
                else:
                    continue
                expressions.append(f"SUM({q} IS NULL) AS {quote(field + '__nulls')}")
            if expressions:
                info["coverage_order"] = "stored values; text extrema are lexical, not timezone-normalized"
                info["coverage"] = reader.query(f"SELECT {', '.join(expressions)} FROM {quote(name)}")
        version_after = conn.execute("PRAGMA data_version").fetchone()[0]
        after = db_stamp(path)
        result["file_after"] = after
        result["snapshot_finished_at_utc"] = utc_now()
        result["concurrent_change_observed"] = before != after or version_before != version_after
        result["cross_database_atomic_snapshot"] = False
        if result["concurrent_change_observed"]:
            result["status"] = "CHANGED_DURING_READ_RETRY"
        result["coverage"] = dict(Counter(i["count"]["status"] for i in result["tables"].values()))
    except (sqlite3.Error, OSError) as exc:
        result.update(status="ERROR", error=str(exc))
    finally:
        reader.conn.close()
    return result


def section(content, heading):
    lines = content.splitlines()
    starts = [i for i, line in enumerate(lines) if line.lstrip("# ").strip() == heading]
    if len(starts) != 1:
        return {"status": "AMBIGUOUS_OR_MISSING", "matches": len(starts)}
    start = starts[0]
    level = len(lines[start]) - len(lines[start].lstrip("#"))
    end = next((i for i in range(start + 1, len(lines))
                if re.match(r"^#{1," + str(level) + r"} ", lines[i])), len(lines))
    full = "\n".join(lines[start:end]).strip()
    return {"status": "OBSERVED_DOCUMENT_CLAIM", "line": start + 1,
            "excerpt": full[:5000], "excerpt_truncated": len(full) > 5000}


def report_signals(value, path="", depth=0):
    """Selected signals only; no article payloads or prediction row exports."""
    signals = {}
    if not isinstance(value, dict) or depth > 5:
        return signals
    keys = {"status", "benchmark_version", "model_version", "dataset_contract", "market_feature_version",
            "label_version", "overall_interpretation", "positive_seed_count", "mean_delta_pct",
            "positive_point_horizons", "failed_horizons", "significant_fail_horizons",
            "primary_point_delta_pct", "primary_block10_ci95", "primary_block_ci95",
            "ci95", "point_delta_pct", "oos_rows", "oos_origin_days", "no_confirmed_alpha"}
    for key, val in value.items():
        loc = f"{path}/{key}"
        if key in keys and not isinstance(val, dict):
            signals[loc] = val
        elif isinstance(val, dict) and key not in {"pooled_metrics", "pooled", "fold_results",
                                                  "raw_payload_json", "metadata_json", "per_seed"}:
            signals.update(report_signals(val, loc, depth + 1))
    return dict(list(signals.items())[:120])


def experiment_evidence(config, texts, files):
    results = []
    for experiment in config.get("experiments", []):
        item = dict(experiment)
        item["document_claim"] = section(texts.get(experiment["document"], ""), experiment["heading"])
        item["artifacts"] = []
        for path in experiment.get("reports", []):
            row = {"path": path, "sha256": files.get(path, {}).get("sha256"),
                   "git": files.get(path, {}).get("git")}
            try:
                report = json.loads(texts[path])
                row.update(status="REPORT_OBSERVED_NOT_REEXECUTED", signals=report_signals(report))
            except (KeyError, ValueError):
                row["status"] = "MISSING_OR_UNREADABLE"
            item["artifacts"].append(row)
        item["scientific_reproduction"] = "NOT_RUN"
        results.append(item)
    return results


def reconcile(config, texts, databases):
    results = []
    for claim in config.get("claims", []):
        row = {"id": claim["id"], "document": claim["document"], "scope": claim["scope"],
               "status": "UNKNOWN", "expected": None, "observed": None}
        matches = re.findall(claim["pattern"], texts.get(claim["document"], ""), re.M)
        values = {int(value.replace(",", "")) for value in matches}
        row["document_pattern"] = claim["pattern"]
        if len(values) != 1:
            row["status"] = "DOCUMENT_MISSING_OR_AMBIGUOUS"
        else:
            row["expected"] = values.pop()
            db = databases.get(claim["database"], {})
            probe = db.get("probes", {}).get(claim["probe"], {})
            row["evidence"] = {"database": claim["database"], "probe": claim["probe"],
                               "field": claim["field"], "query_status": probe.get("status", "MISSING")}
            observed = scalar_result(probe, claim["field"])
            row["observed"] = observed
            if db.get("status") == "CHANGED_DURING_READ_RETRY":
                row["status"] = "UNSTABLE_OBSERVATION"
            elif observed is not None:
                row["status"] = "MATCH" if observed == row["expected"] else "MISMATCH"
        results.append(row)
    for check in config.get("invariants", []):
        db = databases.get(check["database"], {})
        probe = db.get("probes", {}).get(check["probe"], {})
        value = scalar_result(probe, check["field"])
        results.append({
            **check, "kind": "STRUCTURAL_CHECK_NOT_SCIENTIFIC_PROMOTION", "observed": value,
            "status": ("UNSTABLE_OBSERVATION" if db.get("status") == "CHANGED_DURING_READ_RETRY"
                       else "UNKNOWN" if value is None
                       else "MATCH" if value == check["expected"] else "MISMATCH"),
        })
    return results


def artifact_checks(root, databases):
    checks = []
    for db in databases.values():
        fits = db.get("probes", {}).get("frozen_fits", {})
        if fits.get("status") != "EXACT":
            continue
        for row in fits["rows"]:
            item = {"fit_id": row["fit_id"], "status": "UNKNOWN",
                    "artifact_path": row["artifact_path"], "expected_sha256": row["artifact_sha256"]}
            try:
                raw = Path(row["artifact_path"])
                relative = raw.relative_to(root).as_posix() if raw.is_absolute() else raw.as_posix()
                path = safe_path(root, relative)
                if not path.is_file():
                    item["status"] = "MISSING"
                elif path.stat().st_size > 128 * 1024 * 1024:
                    item["status"] = "SKIPPED_SIZE_BUDGET"
                else:
                    before = path.stat()
                    actual = sha_file(path)
                    after = path.stat()
                    item["actual_sha256"] = actual
                    item["status"] = ("CHANGED_DURING_HASH" if before != after else
                                      "MATCH" if actual == row["artifact_sha256"] else "MISMATCH")
            except (ValueError, OSError) as exc:
                item.update(status="UNAVAILABLE_OR_EXTERNAL_PATH", error=str(exc))
            checks.append(item)
    return checks


def report_index(texts, files):
    rows = []
    for path, content in sorted(texts.items()):
        if not path.startswith(("reports/", "data/processed/")) or not path.endswith(".json"):
            continue
        item = {"path": path, "sha256": files[path].get("sha256"), "git": files[path].get("git"),
                "db_snapshot_binding": "NOT_VERIFIED", "status": "REPORT_OBSERVED_NOT_REEXECUTED"}
        try:
            item["signals"] = report_signals(json.loads(content))
        except ValueError:
            item["status"] = "UNREADABLE_JSON"
        rows.append(item)
    return rows


def reconcile_reports(config, texts):
    """Compare documented numbers with saved reports, never claim reproduction."""
    results = []
    for check in config.get("report_checks", []):
        row = {**check, "kind": "DOCUMENT_VS_SAVED_REPORT_NOT_REPRODUCED",
               "status": "UNKNOWN", "expected": None, "observed": None}
        try:
            matches = re.findall(check["pattern"], texts.get(check["document"], ""), re.M)
            expected = {float(v.replace(",", "")) for v in matches}
            if len(expected) != 1:
                row["status"] = "DOCUMENT_MISSING_OR_AMBIGUOUS"
            else:
                row["expected"] = expected.pop()
                actual = json.loads(texts[check["report"]])
                for key in check["pointer"].strip("/").split("/"):
                    actual = actual[int(key)] if isinstance(actual, list) else actual[key]
                if isinstance(actual, bool) or not isinstance(actual, (float, int)) or not math.isfinite(actual):
                    row["status"] = "REPORT_SCHEMA_DRIFT"
                else:
                    row["observed"] = actual
                    row["status"] = "MATCH" if abs(actual-row["expected"]) <= check.get("tolerance", 0) else "MISMATCH"
        except (KeyError, TypeError, ValueError, IndexError):
            row["status"] = "REPORT_MISSING_OR_SCHEMA_DRIFT"
        results.append(row)
    return results


def frozen_config_checks(texts, files, databases):
    results = []
    reg = databases.get("data/processed/market_brain_v009_prospective.db", {})
    probe = reg.get("probes", {}).get("registered_contract", {})
    if probe.get("status") == "EXACT" and len(probe["rows"]) == 1:
        expected = probe["rows"][0]["config_sha256"]
        actual = files.get("config/market_brain_distributional_v009.json", {}).get("sha256")
        results.append({"id": "V009_registered_config_bytes", "expected": expected, "observed": actual,
                        "status": "UNKNOWN" if actual is None else "MATCH" if actual == expected else "MISMATCH"})
    try:
        cfg = json.loads(texts["config/market_brain_distributional_v009.json"])
    except (KeyError, ValueError):
        return results
    sources = {
        "source_v0081_config_sha256": "config/market_brain_distributional_v0081.json",
        "source_v0081_summary_sha256": "reports/market_brain_distributional_v0081/endogenous_closure_v001/benchmark_summary.json",
        "source_v0081_h1_sha256": "reports/market_brain_distributional_v0081/endogenous_closure_v001/h1_benchmark.json",
        "source_v0081_manifest_sha256": "reports/market_brain_distributional_v0081/endogenous_closure_v001/resolved_feature_manifest.json",
    }
    for field, target in sources.items():
        expected, actual = cfg.get(field), files.get(target, {}).get("sha256")
        results.append({"id": "V009_" + field, "path": target, "expected": expected, "observed": actual,
                        "status": "UNKNOWN" if None in (expected, actual) else "MATCH" if expected == actual else "MISMATCH"})
    return results


def cleanup_inventory(files, references):
    candidates, groups = [], defaultdict(list)
    for rel, item in files.items():
        if item.get("sha256") and item.get("bytes", 0) > 0:
            groups[(item["bytes"], item["sha256"])].append(rel)
        if item["kind"] in {"REGENERABLE_CACHE", "DOWNLOAD_METADATA", "ROOT_NOTE_REVIEW", "PACKAGE_REVIEW"}:
            candidates.append({
                "path": rel, "kind": item["kind"], "bytes": item.get("bytes"),
                "git": item.get("git"), "referenced_by": references.get(rel, []),
                "action": ("REVIEW_REGENERABLE" if item["kind"] in {"REGENERABLE_CACHE", "DOWNLOAD_METADATA"}
                           else "REVIEW_ARCHIVE_NOT_DELETE"),
                "automatic_deletion_allowed": False,
            })
    return {
        "mode": "DRY_RUN_ONLY", "candidates": candidates,
        "identical_small_files": [
            {"sha256": key[1], "bytes_each": key[0], "paths": sorted(paths),
             "verdict": "IDENTICAL_BYTES_NOT_PROOF_OF_REDUNDANCY",
             "referenced_by": {p: references.get(p, []) for p in paths}}
            for key, paths in sorted(groups.items()) if len(paths) > 1
        ],
        "not_proof_of_unused": True, "database_row_deletion": "FORBIDDEN_BY_THIS_TOOL",
        "protected": ["all databases and sidecars", "raw lineage", "all experiment evidence including failures",
                      "V009 fits, predictions, outcomes and configurations", "historical documentation"],
        "hash_scope": f"eligible text <= {MAX_TEXT_BYTES} bytes; total budget {HASH_BUDGET_BYTES}",
        "hash_skipped_files": sum(i.get("content_inspection") == "SKIPPED_SIZE_BUDGET" for i in files.values()),
    }


def findings(files, references, docs, claims, databases, experiments, git):
    out = []
    def add(code, details):
        out.append({"code": code, "details": details})
    for doc in docs:
        if doc["status"] != "OBSERVED":
            add("CANONICAL_DOCUMENT_UNAVAILABLE", doc["path"])
    for claim in claims:
        if claim["status"] != "MATCH":
            add("CHECKPOINT_" + claim["status"], claim["id"])
    for rel, item in files.items():
        if item.get("git") == "TRACKED" and item["kind"] in {
            "DATABASE_OR_SIDECAR_PROTECTED", "RAW_EVIDENCE_PROTECTED", "PRIVATE_CONFIGURATION",
            "REGENERABLE_CACHE", "MODEL_OR_DATA_ARTIFACT_PROTECTED",
        }:
            add("TRACKED_LOCAL_ONLY_REVIEW", rel)
        if item.get("git") == "UNTRACKED" and Path(rel).suffix in {".py", ".sql", ".md", ".json"}:
            if item["kind"] not in {"PRIVATE_CONFIGURATION", "REGENERABLE_CACHE"}:
                add("UNCOMMITTED_WORK_NOT_IN_CLONE", rel)
    for path, origins in references.items():
        if (any(mark in path for mark in ("{", "}", "*")) or "YYYYMMDD" in path
                or path.startswith("reports/project_context/")
                or all(origin.startswith("tests/") for origin in origins)):
            # Still retained in references; examples/owned output are not missing dependencies.
            continue
        item = files.get(path)
        if item is None:
            add("REFERENCE_NOT_FOUND_REVIEW_NOT_PROOF_OF_ERROR", {"path": path, "from": origins[:6]})
        elif item.get("git") == "IGNORED" and Path(path).suffix in {".py", ".sql"}:
            add("IGNORED_CODE_DEPENDENCY", {"path": path, "from": origins[:6]})
    for path, db in databases.items():
        if db["status"] != "OBSERVED":
            add("DATABASE_" + db["status"], path)
        if db.get("role") == "UNCLASSIFIED_DATABASE_REVIEW":
            add("NEW_DATABASE_WITHOUT_SEMANTIC_CONTRACT", path)
        for name, table in db.get("tables", {}).items():
            for field, query in {**table.get("groups", {}), "coverage": table.get("coverage", {"status": "EXACT"})}.items():
                if query["status"] not in {"EXACT", "TRUNCATED"}:
                    add("TABLE_PROFILE_NOT_VERIFIED", f"{path}:{name}:{field}")
            if table["type"] == "table" and table["count"]["status"] != "EXACT":
                add("TABLE_COUNT_NOT_VERIFIED", f"{path}:{name}")
    for ex in experiments:
        if ex["document_claim"]["status"] != "OBSERVED_DOCUMENT_CLAIM":
            add("EXPERIMENT_DOCUMENT_ANCHOR_DRIFT", ex["id"])
        for artifact in ex["artifacts"]:
            if artifact["status"] != "REPORT_OBSERVED_NOT_REEXECUTED":
                add("EXPERIMENT_REPORT_MISSING", artifact["path"])
    if git["status"] != "OK":
        add("GIT_INVENTORY_INCOMPLETE", git["status"])
    return out


def signature(files, git):
    relevant = {
        path: {k: item.get(k) for k in ("bytes", "mtime_ns", "kind", "git")}
        for path, item in files.items()
        if item["kind"] not in {"REGENERABLE_CACHE", "DOWNLOAD_METADATA"} and not path.endswith("-shm")
    }
    return sha_bytes(json_text({"files": relevant, "head": git.get("head"),
                                "worktree_changes": git.get("worktree_changes")}).encode())


def build(root, config, query_seconds=3, database_seconds=45, schema_only=False, remote=False, progress=None):
    started = utc_now()
    files, scope = inventory(root)
    git = git_inventory(root, files, remote)
    start_signature = signature(files, git)
    texts, references, content_errors = hash_and_references(root, files)
    docs = documentation(texts, files)
    specs = config.get("databases", {})
    paths = sorted(set(specs) | {p for p, i in files.items() if Path(p).suffix.lower() in DB_SUFFIXES
                                and i["kind"] == "DATABASE_OR_SIDECAR_PROTECTED"})
    databases = {}
    for path in paths:
        if progress:
            progress(f"Inspeccionando {path} (solo lectura)")
        databases[path] = inspect_database(root, path, specs.get(path, {}), query_seconds, database_seconds, schema_only)
    claims = (reconcile(config, texts, databases) + reconcile_reports(config, texts)
              + frozen_config_checks(texts, files, databases))
    experiments = experiment_evidence(config, texts, files)
    frozen = artifact_checks(root, databases)
    issues = findings(files, references, docs, claims, databases, experiments, git)
    issues.extend({"code": "FROZEN_ARTIFACT_" + c["status"], "details": c["fit_id"]}
                  for c in frozen if c["status"] != "MATCH")
    end_files, end_scope = inventory(root)
    end_git = git_inventory(root, end_files)
    changed = start_signature != signature(end_files, end_git)
    if changed:
        issues.append({"code": "PROJECT_CHANGED_DURING_AUDIT", "details": "Repeat after concurrent work settles."})
    if scope["errors"] or end_scope["errors"] or content_errors:
        issues.append({"code": "FILE_INSPECTION_INCOMPLETE", "details": scope["errors"] + end_scope["errors"] + content_errors})
    return {
        "contract_version": VERSION, "started_at_utc": started, "finished_at_utc": utc_now(),
        "status": "REVIEW" if issues else "OBSERVED_NO_FLAGGED_DRIFT", "scientific_promotion": "NOT_EVALUATED",
        "mode": "READ_ONLY_SCHEMA" if schema_only else "READ_ONLY_BOUNDED",
        "scope": scope, "query_seconds": query_seconds, "database_seconds": database_seconds,
        "freshness_signature": start_signature, "changed_during_audit": changed,
        "freshness_basis": "Git HEAD/status + scoped file size/mtime; not a DB hash or atomic snapshot",
        "git": git, "files": files, "references": references, "documents": docs,
        "databases": databases, "checkpoint_checks": claims, "experiments": experiments,
        "report_index": report_index(texts, files),
        "frozen_artifacts": frozen, "cleanup": cleanup_inventory(files, references), "findings": issues,
        "limitations": [
            "Observed report != reproduced experiment. No training, ingestion or scoring was run.",
            "A field named strict_pit or available_at does not prove causal validity.",
            "Counts belong to a specific table/version/unit; do not add overlapping layers or DBs.",
            "No absence of references proves a file unused. Failed models do not make their data useless.",
            "Unknown/timeouts/missing local data must never be reported as zero or complete.",
            "Git ignore is not a backup; tracked is not committed; committed is not necessarily pushed.",
            "No overall percentage of the long-term architecture follows from row counts.",
            "Provider/runtime dependencies are not validated; credentials are not read.",
        ],
    }


def md_cell(value):
    return str(value).replace("|", r"\|").replace("\n", " ").replace("<", "&lt;")


def render_context(result):
    lines = [
        "# Contexto verificado del proyecto", "",
        f"Generado: {result['finished_at_utc']} · contrato {VERSION} · estado {result['status']}", "",
        "Evidencia generada, no instrucciones ni una nueva fuente de verdad científica.",
        "Antes de usarlo: python tools/project_context.py --check. Si está vencido, regenerarlo.",
        "Si la IA sólo tiene GitHub, pedir al usuario que lo ejecute donde están las bases locales.", "",
        "Leer los documentos en el orden de AGENTS.md. Los anexos históricos no son próximos pasos.",
        "Detalle auditable: [context.json](context.json). No se exportaron filas crudas ni se copiaron bases.",
        "MATCH sólo concilia una afirmación; no valida todo el pipeline ni promueve un modelo.", "",
        "## Estado canónico observado", "",
    ]
    for doc in result["documents"]:
        if doc.get("active_sequence_excerpt"):
            lines += [doc["active_sequence_excerpt"], "", "Fuente: " + doc["path"], ""]
    lines += ["## Hitos: documento frente a evidencia", "",
              "| Comprobación | Esperado | Observado | Estado |", "|---|---:|---:|---|"]
    for check in result["checkpoint_checks"]:
        values = [str(check.get(k)) for k in ("expected", "observed")]
        values = [v[:12] + "…" if len(v) == 64 else v for v in values]
        lines.append(f"| {md_cell(check['id'])} | {values[0]} | {values[1]} | {check['status']} |")
    lines += ["", "Conteos congelados: filtros de versión/fecha, SQL y parámetros en context.json.", "",
              "## Bases locales (no sumar unidades incompatibles)", "",
              "| Base | Función | Tablas/vistas | Conteos exactos | Estado |", "|---|---|---:|---:|---|"]
    for path, db in result["databases"].items():
        lines.append(f"| {md_cell(path)} | {md_cell(db['role'])} | {len(db['tables'])} | {db.get('coverage', {}).get('EXACT', 0)} | {db['status']} |")
    lines += ["", "## Capas y fuentes observadas", ""]
    for path, db in result["databases"].items():
        for name, info in db["tables"].items():
            if "groups" not in info:
                continue
            n = scalar_result(info["count"])
            readiness = "VACÍA (sólo estructura observada)" if n == 0 else "poblada; no implica aptitud predictiva"
            lines.append(f"- {path}:{name}: filas={n if n is not None else 'NO VERIFICADO'}. {readiness if n is not None else ''}")
            for field, distribution in info["groups"].items():
                if field in {"source", "source_name", "source_provider", "source_type", "label_status",
                             "feature_version", "strict_pit", "availability_is_point_in_time"}:
                    pairs = [f"{md_cell(r['value'])}: {r['n']}" for r in distribution.get("rows", [])[:8]]
                    tail = " (más grupos en JSON)" if len(distribution.get("rows", [])) > 8 else ""
                    if distribution["status"] == "TRUNCATED":
                        tail += " [DISTRIBUCIÓN TRUNCADA; no todos los grupos están en el JSON]"
                    lines.append(f"  - {field}: {'; '.join(pairs) or distribution['status']}" + tail)
    lines += ["", "## Experimentos: resultado documentado ≠ reproducción", ""]
    for ex in result["experiments"]:
        claim = ex["document_claim"]
        # Keep the boot brief small. Full cited section and machine signals are in JSON.
        excerpt = claim.get("excerpt", "Sección no localizada; revisar.")[:1100]
        lines += [f"### {ex['id']}", "", f"Fuente: {ex['document']} · sección {ex['heading']}.",
                  excerpt, "", "Reportes: " + ", ".join(f"{a['path']} ({a['status']})" for a in ex["artifacts"]), ""]
    live = result["git"].get("live_remote", {})
    lines += ["## Git y recuperación", "", f"HEAD local: {result['git'].get('head', 'DESCONOCIDO')}.",
              f"GitHub en vivo: {live.get('status')}; coincide con HEAD: {live.get('equals_local_head', 'no comprobado')}.",
              "Ver REPOSITORY.md: archivos presentes, versionados y sólo locales.", "",
              "## Hallazgos de revisión", ""]
    counts = Counter(row["code"] for row in result["findings"])
    lines += [f"- {code}: {n}" for code, n in sorted(counts.items())]
    lines += ["", "## Límites", ""] + ["- " + s for s in result["limitations"]]
    return "\n".join(lines) + "\n"


def render_repository(result):
    files = result["files"]
    totals = Counter(item.get("git", "UNKNOWN") for item in files.values())
    lines = ["# Repositorio frente a entorno local", "",
             "Git: código, contratos, esquemas, tests y reportes pequeños revisados.",
             "Respaldo local separado: bases/WAL/SHM, raw, credenciales, entornos, modelos y salidas masivas.",
             "Ignorado no significa prescindible. Este auditor no hace commit, push, fetch ni cambios al índice.", "",
             f"Archivos por estado Git: {dict(totals)}", "",
             "## Cambios locales (no necesariamente publicados)", ""]
    lines += ["- " + md_cell(s) for s in result["git"].get("worktree_changes", [])[:100]]
    lines += ["", "## Bases protegidas", ""]
    lines += ["- " + md_cell(p) for p, i in files.items() if i["kind"] == "DATABASE_OR_SIDECAR_PROTECTED"]
    lines += ["", "## Dependencias ignoradas / material local versionado por error", ""]
    lines += ["- " + md_cell(json.dumps(f, ensure_ascii=False)) for f in result["findings"]
              if f["code"] in {"IGNORED_CODE_DEPENDENCY", "TRACKED_LOCAL_ONLY_REVIEW"}]
    lines += ["", "Inventario completo, referencias, hashes pequeños y estado remoto: context.json.",
              "Referencias ausentes pueden ser históricas, plantillas o salidas no ejecutadas: no recrearlas automáticamente."]
    return "\n".join(lines) + "\n"


def render_cleanup(result):
    cleanup = result["cleanup"]
    lines = ["# Limpieza — sólo revisión", "", "No se borró ni movió ningún archivo.",
             "Fallo científico ≠ datos inútiles. Se protegen experimentos fallidos, raw, bases y V009.",
             "Nunca borrar WAL/SHM de bases activas. Bytes idénticos no prueban redundancia funcional.", "",
             "## Candidatos", ""]
    grouped = defaultdict(list)
    for item in cleanup["candidates"]:
        grouped[item["kind"]].append(item)
    for kind, items in grouped.items():
        lines.append(f"- {kind}: {len(items)} archivos, {sum(i.get('bytes') or 0 for i in items)} bytes.")
        if kind != "REGENERABLE_CACHE":
            lines += [f"  - {md_cell(i['path'])} ({i['action']})" for i in items[:30]]
    lines += ["", "## Archivos pequeños idénticos", ""]
    lines += ["- " + " / ".join(md_cell(p) for p in group["paths"])
              for group in cleanup["identical_small_files"][:40]]
    lines += ["", "## Procedimiento seguro", "",
              "1. Elegir rutas exactas del JSON; verificar otra vez hashes, referencias y cambios Git.",
              "2. Para evidencia científica, demostrar reconstrucción/backup antes de proponer eliminación.",
              "3. Archivar notas obsoletas sin alterar contratos históricos; revisar imports y tests.",
              "4. Pedir una decisión concreta para candidatos ambiguos. No hay opción --delete.",
              "5. Regenerar contexto después de limpiar."]
    return "\n".join(lines) + "\n"


def write_outputs(root, result):
    out = safe_path(root, OUTPUT)
    marker = safe_path(root, OUTPUT + "/.context-output")
    if out.exists() and any(out.iterdir()) and not marker.is_file():
        raise ValueError("output contains unowned files; refusing to overwrite")
    if marker.is_file() and marker.read_text(encoding="utf-8").strip() != VERSION:
        raise ValueError("output belongs to a different contract; review before replacing")
    out.mkdir(parents=True, exist_ok=True)
    lock = safe_path(root, OUTPUT + "/.write-lock")
    # Do not let two AIs interleave output files. A leftover lock needs human review.
    lock_fd = os.open(lock, os.O_CREAT | os.O_EXCL | os.O_WRONLY, 0o600)
    os.close(lock_fd)
    try:
        payloads = {
            ".context-output": VERSION + "\n",
            "CONTEXT.md": redact(render_context(result)),
            "REPOSITORY.md": redact(render_repository(result)),
            "CLEANUP.md": redact(render_cleanup(result)),
        }
        complete = redact(result)
        complete["output_sha256"] = {name: sha_bytes(content.encode("utf-8"))
                                     for name, content in payloads.items()}
        # JSON is the commit marker: --check verifies the companion file hashes.
        payloads["context.json"] = json_text(complete)
        for name, content in payloads.items():
            dest = safe_path(root, OUTPUT + "/" + name)
            fd, temporary = tempfile.mkstemp(prefix=".context-", dir=out)
            try:
                with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as stream:
                    stream.write(content)
                os.replace(temporary, dest)
            finally:
                if os.path.exists(temporary):
                    os.unlink(temporary)
    finally:
        lock.unlink()
    return out


def check_freshness(root, max_age_hours=24):
    path = safe_path(root, OUTPUT + "/context.json")
    if not path.is_file():
        return {"status": "MISSING", "action": "python tools/project_context.py"}
    try:
        prior = json.loads(path.read_text(encoding="utf-8"))
        age = (datetime.now(timezone.utc) - datetime.fromisoformat(prior["finished_at_utc"])).total_seconds() / 3600
        files, scope = inventory(root)
        git = git_inventory(root, files)
        reasons = []
        if safe_path(root, OUTPUT + "/.write-lock").exists():
            reasons.append("output_write_in_progress_or_interrupted")
        for name, expected_hash in prior.get("output_sha256", {}).items():
            companion = safe_path(root, OUTPUT + "/" + name)
            if not companion.is_file() or sha_file(companion) != expected_hash:
                reasons.append("output_incomplete_or_modified")
                break
        if prior.get("contract_version") != VERSION:
            reasons.append("contract_version_changed")
        if prior.get("changed_during_audit"):
            reasons.append("prior_run_observed_concurrent_change")
        if scope["errors"] or git["status"] != "OK":
            reasons.append("cannot_verify_all_dependencies")
        if signature(files, git) != prior["freshness_signature"]:
            reasons.append("files_or_git_changed")
        if age > max_age_hours or age < 0:
            reasons.append("expired_or_clock_changed")
        return {"status": "STALE" if reasons else "FRESH_WITHIN_METADATA_SCOPE", "reasons": reasons,
                "age_hours": round(age, 3), "prior_audit_status": prior["status"],
                "scientific_validity": "NOT_REEVALUATED"}
    except (ValueError, KeyError, OSError) as exc:
        return {"status": "UNREADABLE", "error": str(exc)}


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--root", type=Path, default=Path(__file__).resolve().parents[1])
    parser.add_argument("--config", default="config/project_context_v001.json")
    parser.add_argument("--query-seconds", type=float, default=3)
    parser.add_argument("--database-seconds", type=float, default=45)
    parser.add_argument("--schema-only", action="store_true")
    parser.add_argument("--remote", action="store_true", help="opt-in read-only live origin/main check")
    parser.add_argument("--check", action="store_true", help="check freshness without DB queries")
    parser.add_argument("--max-age-hours", type=float, default=24)
    args = parser.parse_args(argv)
    if any(not math.isfinite(v) or v <= 0 for v in (args.query_seconds, args.database_seconds, args.max_age_hours)):
        parser.error("time budgets must be positive")
    root = args.root.resolve()
    try:
        if args.check:
            result = check_freshness(root, args.max_age_hours)
            print(json_text(result), end="")
            return 0 if result["status"] == "FRESH_WITHIN_METADATA_SCOPE" else 3
        config = json.loads(safe_path(root, args.config).read_text(encoding="utf-8"))
        if config.get("contract_version") != VERSION:
            raise ValueError("unsupported context contract version")
        result = build(root, config, args.query_seconds, args.database_seconds, args.schema_only, args.remote,
                       progress=lambda s: print(s, file=sys.stderr, flush=True))
        out = write_outputs(root, result)
        print(json_text({
            "status": result["status"], "output": str(out / "CONTEXT.md"),
            "databases": len(result["databases"]), "findings": len(result["findings"]),
            "checkpoint_statuses": dict(Counter(c["status"] for c in result["checkpoint_checks"])),
            "databases_mutated": False, "scientific_promotion": "NOT_EVALUATED",
        }), end="")
        return 2 if result["status"] == "REVIEW" else 0
    except (OSError, ValueError, sqlite3.Error) as exc:
        print(json_text({"status": "ERROR", "error": redact(str(exc))}), end="")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())

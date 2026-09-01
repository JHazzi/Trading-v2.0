from __future__ import annotations

import copy
import hashlib
import importlib.util
import io
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from ingestion.public_information.intake_v001 import (
    ABSOLUTE_STORAGE_CAP_BYTES,
    CONTRACT_VERSION,
    MODEL_VISIBILITY,
    IntakeError,
    audit_snapshot,
    build_plan,
    construct_manifest,
    initialize_catalog,
    latest_manifest,
    persist_manifest,
    sample_snapshot,
    storage_gate,
    stream_download,
    validate_config,
    validate_write_path,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
SCHEMA = REPO_ROOT / "database" / "public_information_catalog_v001_schema.sql"


def base_config(root: Path) -> dict:
    return {
        "contract_version": CONTRACT_VERSION,
        "purpose": "test",
        "training_authorized": False,
        "feature_visibility": MODEL_VISIBILITY,
        "paths": {
            "catalog_db": str(root / "database" / "catalog.db"),
            "catalog_schema": str(SCHEMA),
            "raw_root": str(root / "raw"),
            "lake_root": str(root / "lake"),
            "report_root": str(root / "reports"),
        },
        "storage": {
            "hard_cap_bytes": ABSOLUTE_STORAGE_CAP_BYTES,
            "absolute_max_allowed_bytes": ABSOLUTE_STORAGE_CAP_BYTES,
            "minimum_free_after_operation_bytes": 0,
            "download_chunk_bytes": 3,
            "progress_every_bytes": 4,
            "preallocate_space": False,
            "managed_roots": [str(root / "raw"), str(root / "lake")],
        },
        "isolation": {
            "source_databases_read_only_and_not_opened": [str(root / "source.db")],
            "protected_write_paths": [
                str(root / "source.db"),
                str(root / "core.db"),
                str(root / "v009.db"),
                str(root / "v009_reports"),
            ],
            "forbidden_write_path_tokens": ["market_data_v2.db", "v009.db"],
        },
        "economic_policies": {
            "intraday_primary_candidate": "alpaca_feed_unknown_until_audit",
            "cross_source_price_policy": "PRESERVE_EACH_SOURCE_NO_MEDIAN_NO_OVERWRITE",
            "volume_policy": "NEVER_BLEND_ACROSS_IEX_SIP_YAHOO",
            "canonical_bar_status": "BLOCKED",
            "news_duplicate_policy": "PRESERVE",
            "news_causal_policy": "BLOCKED",
        },
        "initial_download_profiles": [["bars", "full"], ["news", "priority"]],
        "datasets": {
            "bars": {
                "data_kind": "bars_1m",
                "repo_type": "dataset",
                "repo_id": "owner/bars",
                "revision": "main",
                "token_env": "HF_TOKEN_TEST_UNUSED",
                "token_optional": True,
                "declared_license": "mit",
                "rights_status": "REVIEW",
                "causal_status": "PIT_FALSE",
                "expected_columns": ["open", "close"],
                "profiles": {
                    "full": {
                        "include": ["*.parquet"],
                        "exclude": ["skip*.parquet"],
                        "estimated_bytes": 12,
                    }
                },
            },
            "news": {
                "data_kind": "news_documents",
                "repo_type": "dataset",
                "repo_id": "owner/news",
                "revision": "abc",
                "token_env": "HF_TOKEN_TEST_UNUSED",
                "token_optional": True,
                "declared_license": "other",
                "rights_status": "RESEARCH_ONLY",
                "causal_status": "MIXED",
                "expected_columns": ["date", "text", "extra_fields"],
                "profiles": {
                    "priority": {
                        "include": ["data/a/*.parquet"],
                        "exclude": [],
                        "estimated_bytes": 15,
                    }
                },
            },
        },
        "samples": {},
    }


def fake_manifest(config: dict, *, size: int = 7) -> dict:
    dataset = config["datasets"]["bars"]
    return construct_manifest(
        "bars",
        "full",
        dataset,
        dataset["profiles"]["full"],
        {"sha": "f" * 40, "cardData": {"license": "mit"}},
        [
            {
                "type": "file",
                "path": "bars.parquet",
                "size": size,
                "oid": "blob",
                "lfs": {"oid": "sha256:" + "a" * 64, "size": size},
            },
            {"type": "file", "path": "skip_bad.parquet", "size": 10},
            {"type": "file", "path": "README.md", "size": 10},
        ],
    )


class FakeResponse:
    def __init__(self, payload: bytes, status: int):
        self._handle = io.BytesIO(payload)
        self.status = status
        self.headers = {}

    def read(self, size: int = -1) -> bytes:
        return self._handle.read(size)

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return None


class PublicInformationIntakeV001Tests(unittest.TestCase):
    def test_config_hard_blocks_training_median_and_over_100_gib(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = base_config(Path(td))
            self.assertTrue(validate_config(config)["valid"])
            too_large = copy.deepcopy(config)
            too_large["storage"]["hard_cap_bytes"] = ABSOLUTE_STORAGE_CAP_BYTES + 1
            self.assertFalse(validate_config(too_large)["valid"])
            median = copy.deepcopy(config)
            median["economic_policies"]["cross_source_price_policy"] = "MEDIAN"
            self.assertFalse(validate_config(median)["valid"])
            training = copy.deepcopy(config)
            training["training_authorized"] = True
            self.assertFalse(validate_config(training)["valid"])

    def test_write_guard_rejects_source_core_and_v009(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = base_config(root)
            allowed = root / "raw" / "dataset" / "file.parquet"
            self.assertEqual(validate_write_path(root, config, allowed), allowed.resolve())
            for path in (root / "source.db", root / "core.db", root / "v009.db"):
                with self.assertRaises(IntakeError):
                    validate_write_path(root, config, path)

    def test_manifest_filters_files_and_pins_resolved_revision(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = base_config(Path(td))
            manifest = fake_manifest(config)
            self.assertEqual(manifest["selected_file_count"], 1)
            self.assertEqual(manifest["files"][0]["repo_path"], "bars.parquet")
            self.assertEqual(manifest["resolved_revision"], "f" * 40)
            self.assertEqual(len(manifest["manifest_sha256"]), 64)
            self.assertEqual(manifest["files"][0]["lfs_sha256"], "a" * 64)
            self.assertFalse(manifest["training_authorized"])

    def test_catalog_and_manifest_are_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = base_config(root)
            first = initialize_catalog(root, config)
            second = initialize_catalog(root, config)
            self.assertEqual(first["schema_sha256"], second["schema_sha256"])
            manifest = fake_manifest(config)
            one = persist_manifest(root, config, manifest)
            two = persist_manifest(root, config, manifest)
            self.assertEqual(one["snapshot_id"], two["snapshot_id"])
            observed = latest_manifest(root, config, "bars", "full")
            self.assertEqual(observed["manifest_sha256"], manifest["manifest_sha256"])
            with sqlite3.connect(root / "database" / "catalog.db") as conn:
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM dataset_snapshots").fetchone()[0], 1)
                self.assertEqual(conn.execute("SELECT COUNT(*) FROM snapshot_files").fetchone()[0], 1)

    def test_profiles_at_same_revision_share_object_bytes(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = base_config(root)
            first = fake_manifest(config)
            second = copy.deepcopy(first)
            second["profile_name"] = "secondary"
            basis = dict(second)
            for key in (
                "manifest_sha256",
                "snapshot_id",
                "selected_file_count",
                "selected_bytes",
                "training_authorized",
            ):
                basis.pop(key, None)
            digest = hashlib.sha256(
                json.dumps(
                    basis,
                    sort_keys=True,
                    separators=(",", ":"),
                    ensure_ascii=False,
                    allow_nan=False,
                ).encode("utf-8")
            ).hexdigest()
            second["manifest_sha256"] = digest
            second["snapshot_id"] = f"snapshot_{digest[:24]}"
            persist_manifest(root, config, first)
            persist_manifest(root, config, second)
            with sqlite3.connect(root / "database" / "catalog.db") as conn:
                paths = conn.execute(
                    "SELECT DISTINCT local_path FROM snapshot_files ORDER BY local_path"
                ).fetchall()
            self.assertEqual(len(paths), 1)
            self.assertIn(first["resolved_revision"], paths[0][0])

    def test_storage_gate_counts_existing_partials_without_preallocation(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = base_config(root)
            config["storage"]["hard_cap_bytes"] = 10
            (root / "raw").mkdir()
            (root / "raw" / "partial.part").write_bytes(b"123456")
            gate = storage_gate(root, config, 5)
            self.assertFalse(gate["pass"])
            self.assertEqual(gate["projected_managed_bytes"], 11)
            self.assertFalse(gate["preallocation_performed"])

    def test_stream_download_resumes_atomically_and_is_idempotent(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "bars.parquet"
            payload = b"abcdefghij"
            partial = destination.with_name(destination.name + ".part")
            partial.write_bytes(payload[:4])

            def opener(url, headers, timeout):
                self.assertEqual(headers["Range"], "bytes=4-")
                return FakeResponse(payload[4:], 206)

            result = stream_download(
                "https://example.invalid/file",
                destination,
                len(payload),
                hashlib.sha256(payload).hexdigest(),
                None,
                3,
                4,
                opener=opener,
            )
            self.assertEqual(result["status"], "COMPLETE")
            self.assertEqual(destination.read_bytes(), payload)
            self.assertFalse(partial.exists())
            second = stream_download(
                "https://example.invalid/file",
                destination,
                len(payload),
                hashlib.sha256(payload).hexdigest(),
                None,
                3,
                4,
                opener=lambda *args: self.fail("network should not be called"),
            )
            self.assertEqual(second["status"], "ALREADY_COMPLETE")

    def test_unhonored_range_preserves_partial(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            destination = Path(td) / "news.parquet"
            partial = destination.with_name(destination.name + ".part")
            partial.write_bytes(b"abc")
            with self.assertRaises(IntakeError):
                stream_download(
                    "https://example.invalid/file",
                    destination,
                    6,
                    None,
                    None,
                    3,
                    4,
                    opener=lambda *args: FakeResponse(b"abcdef", 200),
                )
            self.assertEqual(partial.read_bytes(), b"abc")

    def test_integrity_audit_does_not_require_duckdb_or_authorize_model(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = base_config(root)
            manifest = fake_manifest(config, size=7)
            persist_manifest(root, config, manifest)
            manifest_path = Path(
                latest_manifest(root, config, "bars", "full")["files"][0]["repo_path"]
            )
            del manifest_path
            local = (
                root
                / "raw"
                / "objects"
                / "bars"
                / manifest["resolved_revision"]
                / "files"
                / "bars.parquet"
            )
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(b"1234567")
            result = audit_snapshot(root, config, "bars", "full", level="integrity")
            self.assertEqual(result["status"], "PASS_RAW_FILE_INTEGRITY")
            self.assertFalse(result["training_authorized"])

    def test_plan_is_data_only_and_v009_blind(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = base_config(root)
            for name in ("source.db", "core.db", "v009.db"):
                (root / name).write_bytes(b"protected")
            before = {name: (root / name).read_bytes() for name in ("source.db", "core.db", "v009.db")}
            plan = build_plan(root, config)
            initialize_catalog(root, config)
            after = {name: (root / name).read_bytes() for name in before}
            self.assertEqual(before, after)
            self.assertFalse(plan["training_authorized"])
            self.assertIn("NONE", plan["v009_interaction"])
            self.assertEqual(
                plan["economic_policies"]["cross_source_price_policy"],
                "PRESERVE_EACH_SOURCE_NO_MEDIAN_NO_OVERWRITE",
            )

    @unittest.skipUnless(importlib.util.find_spec("duckdb"), "optional DuckDB not installed")
    def test_real_duckdb_full_bars_audit_and_sample(self) -> None:
        import duckdb

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = base_config(root)
            config["datasets"]["bars"]["expected_columns"] = [
                "open", "high", "low", "close", "volume", "trade_count",
                "vol_weighted_avg_price", "timestamp", "ticker", "name",
            ]
            config["samples"] = {
                "bars": {
                    "dataset_key": "bars",
                    "profile": "full",
                    "tickers": ["AAA"],
                    "windows_utc": [[
                        "2020-01-01T00:00:00+00:00",
                        "2020-01-03T00:00:00+00:00",
                    ]],
                }
            }
            source = root / "source.parquet"
            con = duckdb.connect(database=":memory:")
            con.execute(
                """
                CREATE TABLE bars AS SELECT
                  10.0::DOUBLE AS open, 11.0::DOUBLE AS high,
                  9.0::DOUBLE AS low, 10.5::DOUBLE AS close,
                  100::UBIGINT AS volume, 5::UBIGINT AS trade_count,
                  10.2::DOUBLE AS vol_weighted_avg_price,
                  TIMESTAMPTZ '2020-01-02T14:30:00Z' AS timestamp,
                  'AAA'::VARCHAR AS ticker, 'Alpha'::VARCHAR AS name
                """
            )
            con.execute(f"COPY bars TO '{source}' (FORMAT PARQUET)")
            con.close()
            dataset = config["datasets"]["bars"]
            manifest = construct_manifest(
                "bars",
                "full",
                dataset,
                dataset["profiles"]["full"],
                {"sha": "f" * 40, "cardData": {"license": "mit"}},
                [{"type": "file", "path": "bars.parquet", "size": source.stat().st_size}],
            )
            persist_manifest(root, config, manifest)
            local = (
                root / "raw" / "objects" / "bars" / manifest["resolved_revision"]
                / "files" / "bars.parquet"
            )
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(source.read_bytes())
            audit = audit_snapshot(root, config, "bars", "full", level="full")
            self.assertEqual(audit["status"], "PASS_BARS_STRUCTURAL_CONTENT_AUDIT")
            self.assertEqual(audit["bars_content"]["ohlc_envelope_violations"], 0)
            sample = sample_snapshot(root, config, "bars")
            self.assertEqual(sample["status"], "PASS_STRUCTURAL_SAMPLE_CREATED")
            self.assertEqual(sample["metrics"]["rows"], 1)

    @unittest.skipUnless(importlib.util.find_spec("duckdb"), "optional DuckDB not installed")
    def test_real_duckdb_full_news_audit_accepts_structured_extra_fields(self) -> None:
        import duckdb

        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = base_config(root)
            config["samples"] = {
                "news": {
                    "dataset_key": "news",
                    "profile": "priority",
                    "rows": 2,
                    "seed": 1701,
                }
            }
            source = root / "source_news.parquet"
            con = duckdb.connect(database=":memory:")
            con.execute(
                """
                CREATE TABLE news AS
                SELECT DATE '2020-01-02' AS date,
                       'Alpha announces results'::VARCHAR AS text,
                       {'source': 'wire', 'ticker': 'AAA'} AS extra_fields
                UNION ALL
                SELECT DATE '2020-01-03', 'Beta files report',
                       {'source': 'filing', 'ticker': 'BBB'}
                """
            )
            con.execute(f"COPY news TO '{source}' (FORMAT PARQUET)")
            con.close()
            dataset = config["datasets"]["news"]
            manifest = construct_manifest(
                "news",
                "priority",
                dataset,
                dataset["profiles"]["priority"],
                {"sha": "e" * 40, "cardData": {"license": "other"}},
                [{
                    "type": "file",
                    "path": "data/a/news.parquet",
                    "size": source.stat().st_size,
                }],
            )
            persist_manifest(root, config, manifest)
            local = (
                root / "raw" / "objects" / "news" / manifest["resolved_revision"]
                / "files" / "data" / "a" / "news.parquet"
            )
            local.parent.mkdir(parents=True, exist_ok=True)
            local.write_bytes(source.read_bytes())
            audit = audit_snapshot(root, config, "news", "priority", level="full")
            self.assertEqual(audit["status"], "REVIEW_NEWS_RIGHTS_TIME_AND_DEDUP_REQUIRED")
            self.assertEqual(audit["news_content"]["missing_extra_fields_rows"], 0)
            sample = sample_snapshot(root, config, "news")
            self.assertEqual(sample["status"], "PASS_STRUCTURAL_SAMPLE_CREATED")
            self.assertEqual(sample["metrics"]["rows"], 2)


if __name__ == "__main__":
    unittest.main()

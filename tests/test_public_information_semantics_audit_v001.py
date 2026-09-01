from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import duckdb

from tools.public_information_semantics_audit_v001 import (
    MODEL_VISIBILITY,
    VERSION,
    build_plan,
    run_audit,
    validate_config,
)


REPO_ROOT = Path(__file__).resolve().parents[1]


def _sqlite(path: Path, script: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(script)


def _parquets(root: Path) -> tuple[Path, Path]:
    bars = root / "raw" / "bars.parquet"
    news = root / "raw" / "news.parquet"
    bars.parent.mkdir(parents=True, exist_ok=True)
    with duckdb.connect() as connection:
        connection.execute(
            """
            CREATE TABLE bars(
              ticker VARCHAR,timestamp TIMESTAMPTZ,open DOUBLE,high DOUBLE,
              low DOUBLE,close DOUBLE,volume DOUBLE,trade_count BIGINT,
              vol_weighted_avg_price DOUBLE
            )
            """
        )
        rows = [
            ("AAPL", "2024-01-02 13:00:00+00", 99, 99, 99, 99, 2, 1, 99),
            ("AAPL", "2024-01-02 14:30:00+00", 100, 102, 99, 101, 10, 2, 100.5),
            ("AAPL", "2024-01-02 14:31:00+00", 101, 103, 100, 102, 20, 3, 101.5),
            ("AAPL", "2024-01-02 21:00:00+00", 102, 102, 102, 102, 1, 1, 102),
            ("AAPL", "2024-01-03 14:30:00+00", 105, 106, 104, 105, 12, 2, 105),
            ("AAPL", "2024-01-03 14:31:00+00", 105, 107, 104, 106, 18, 2, 105.5),
            ("NOCORE", "2024-01-02 14:30:00+00", 10, 11, 9, 10, 5, 1, 10),
            ("AAPL", "2024-01-06 14:30:00+00", 100, 101, 99, 100, 5, 1, 100),
        ]
        connection.executemany("INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?)", rows)
        connection.execute(f"COPY bars TO '{bars.as_posix()}' (FORMAT PARQUET)")
        connection.execute(
            "CREATE TABLE news(date VARCHAR,text VARCHAR,extra_fields VARCHAR)"
        )
        fnspid = {
            "dataset": "fnspid_news",
            "dataset_source": "hf:Zihan1004/FNSPID",
            "publisher": "Benzinga Insights",
            "url": "https://www.benzinga.com/aapl-story",
            "stocks": ["AAPL"],
            "time_precision": "minute",
            "tz_hint": "UTC",
        }
        fnspid_replica = {**fnspid, "stocks": ["MSFT"]}
        coarse = {
            "dataset": "yahoo_finance_felixdrinkall",
            "dataset_source": "hf:example/yahoo",
            "publisher": "Reuters",
            "url": "https://finance.yahoo.com/reuters-story",
            "stocks": ["AAPL", "UNKNOWN"],
            "time_precision": "day",
            "tz_hint": "unknown",
        }
        news_rows = [
            ("2024-01-02T15:01:00Z", "Apple launches a product", json.dumps(fnspid)),
            ("2024-01-02T15:01:00Z", "Apple launches a product", json.dumps(fnspid_replica)),
            ("2024-01-03", "A separate Reuters report", json.dumps(coarse)),
        ]
        connection.executemany("INSERT INTO news VALUES (?,?,?)", news_rows)
        connection.execute(f"COPY news TO '{news.as_posix()}' (FORMAT PARQUET)")
    return bars, news


def _fixture(root: Path) -> dict:
    bars, news = _parquets(root)
    market = root / "market.db"
    core = root / "core.db"
    graph = root / "graph.db"
    catalog = root / "catalog.db"
    _sqlite(
        market,
        """
        CREATE TABLE assets(asset_id INTEGER PRIMARY KEY,ticker TEXT);
        INSERT INTO assets VALUES(1,'AAPL'),(2,'MSFT');
        CREATE TABLE asset_identifier_history(
          identifier_history_id TEXT,asset_id INTEGER,identifier_type TEXT,
          identifier_value TEXT,source_id TEXT,valid_from TEXT,valid_to TEXT
        );
        INSERT INTO asset_identifier_history VALUES('i1',1,'ticker','AAPL','test',NULL,NULL);
        CREATE TABLE price_bar_versions(
          price_bar_version_id TEXT PRIMARY KEY,open REAL,high REAL,low REAL,
          close REAL,volume REAL
        );
        CREATE TABLE price_bar_observations(
          price_observation_id TEXT PRIMARY KEY,price_bar_version_id TEXT,
          source_id TEXT,asset_id INTEGER,trading_day TEXT,
          observation_sequence INTEGER,observed_at TEXT
        );
        INSERT INTO price_bar_versions VALUES
          ('v1',100,103,99,102,30),('v2',105,107,104,106,30);
        INSERT INTO price_bar_observations VALUES
          ('o1','v1','yahoo_finance',1,'2024-01-02',1,'2024-01-02T22:00:00Z'),
          ('o2','v2','yahoo_finance',1,'2024-01-03',1,'2024-01-03T22:00:00Z');
        CREATE TABLE corporate_action_versions(
          asset_id INTEGER,effective_trading_day TEXT,is_present INTEGER
        );
        INSERT INTO corporate_action_versions VALUES(1,'2024-01-03',1);
        """,
    )
    _sqlite(
        core,
        """
        CREATE TABLE market_daily_v003_states(
          asset_id INTEGER,ticker TEXT,sector TEXT,trading_day TEXT
        );
        INSERT INTO market_daily_v003_states VALUES
          (1,'AAPL','Technology','2024-01-02'),
          (1,'AAPL','Technology','2024-01-03'),
          (2,'MSFT','Technology','2024-01-02');
        """,
    )
    _sqlite(
        graph,
        """
        CREATE TABLE identity_evidence_buckets(
          identity_bucket_id TEXT,registrant_asset_id INTEGER,
          registrant_ticker TEXT,identity_status TEXT
        );
        INSERT INTO identity_evidence_buckets VALUES
          ('g1',1,'AAPL','evidence_bucket_not_canonical');
        """,
    )
    _sqlite(
        catalog,
        """
        CREATE TABLE dataset_snapshots(
          snapshot_id TEXT,dataset_key TEXT,profile_name TEXT,requested_revision TEXT,
          resolved_revision TEXT,manifest_sha256 TEXT,selected_file_count INTEGER,
          selected_bytes INTEGER,manifest_path TEXT,first_registered_at_utc TEXT,
          last_verified_at_utc TEXT
        );
        CREATE TABLE snapshot_files(
          snapshot_id TEXT,repo_path TEXT,size_bytes INTEGER,oid TEXT,
          lfs_sha256 TEXT,xet_hash TEXT,download_url TEXT,local_path TEXT,
          status TEXT,local_size_bytes INTEGER,local_sha256 TEXT
        );
        """,
    )
    with sqlite3.connect(catalog) as connection:
        for key, profile, path in (("bars", "full", bars), ("news", "priority", news)):
            digest = key * 8
            manifest = root / f"{key}_manifest.json"
            manifest.write_text(json.dumps({"manifest_sha256": digest}), encoding="utf-8")
            connection.execute(
                "INSERT INTO dataset_snapshots VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (f"s_{key}", key, profile, "main", "rev", digest, 1,
                 path.stat().st_size, str(manifest), "2024-01-01", "2024-01-02"),
            )
            connection.execute(
                "INSERT INTO snapshot_files VALUES(?,?,?,?,?,?,?,?,?,?,?)",
                (f"s_{key}", path.name, path.stat().st_size, None, None, None,
                 "https://example.invalid", str(path), "COMPLETE",
                 path.stat().st_size, None),
            )
    return {
        "version": VERSION,
        "training_authorized": False,
        "materialization_authorized": False,
        "feature_visibility": MODEL_VISIBILITY,
        "paths": {
            "intake_config": str(root / "unused.json"),
            "intake_catalog_db": str(catalog),
            "market_db": str(market),
            "core_db": str(core),
            "graph_identity_db": str(graph),
            "report_dir": str(root / "reports"),
        },
        "snapshots": {
            "bars": {"dataset_key": "bars", "profile_name": "full"},
            "news": {"dataset_key": "news", "profile_name": "priority"},
        },
        "bar_contract": {
            "exchange_timezone": "America/New_York",
            "timestamp_semantics": "bar_start_assumption_pending_provenance_confirmation",
            "session_windows_local": {
                "premarket": ["04:00:00", "09:30:00"],
                "rth": ["09:30:00", "16:00:00"],
                "afterhours": ["16:00:00", "20:00:00"],
            },
            "expected_full_rth_minutes": 390,
            "cross_source_policy": "PRESERVE_BOTH_NO_MEDIAN_NO_OVERWRITE",
            "volume_policy": "REPORT_ONLY_NEVER_BLEND",
            "price_difference_thresholds_pct": [0.01, 1.0],
            "opening_gap_threshold_pct": 3.0,
        },
        "identity_contract": {"primary_mapping": "exact", "graph_identity_role": "evidence"},
        "news_contract": {
            "fnspid_collection_path": "hf:Zihan1004/FNSPID",
            "fnspid_declared_collection_route": "NASDAQ_WITH_DOCUMENT_FIELDS",
            "source_asymmetry_policy": "MEASURE_AND_PRESERVE_NOT_A_BLOCKER_NOT_AN_INDEPENDENCE_CLAIM",
            "publisher_reliability_policy": "NOT_HARDCODED_NOT_SCORED_BY_THIS_AUDIT",
            "minute_midnight_status": "SUSPECT_PLACEHOLDER_OR_COARSE_TIME",
            "day_available_at_policy": "NEXT_SESSION_PROXY_ONLY_IN_FUTURE_MATERIALIZER",
            "deduplication_policy": "PRESERVE_DOCUMENTS_CLUSTER_EVIDENCE_DO_NOT_COUNT_DUPLICATES_AS_INDEPENDENT_EVENTS",
            "top_n_source_rows": 20,
            "syndication_prefix_chars": 20,
        },
        "guards": {
            "forbidden_path_tokens": ["market_brain_v009", "prospective_prediction"],
            "forbidden_outputs": [str(root / "v009")],
        },
        "outputs": ["plan.json", "audit.json"],
    }


class PublicInformationSemanticsAuditV001Tests(unittest.TestCase):
    def test_config_blocks_training_and_protected_path_but_not_guard_words(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            config = _fixture(Path(td))
            self.assertTrue(validate_config(Path(td), config)["valid"])
            training = copy.deepcopy(config)
            training["training_authorized"] = True
            self.assertFalse(validate_config(Path(td), training)["valid"])
            protected = copy.deepcopy(config)
            protected["paths"]["report_dir"] = str(Path(td) / "v009" / "bad")
            self.assertFalse(validate_config(Path(td), protected)["valid"])

    def test_plan_and_full_audit_preserve_sources_and_separate_lineage(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = _fixture(root)
            source_state = {
                key: Path(config["paths"][key]).stat().st_mtime_ns
                for key in ("intake_catalog_db", "market_db", "core_db", "graph_identity_db")
            }
            plan = build_plan(root, config)
            self.assertEqual(plan["status"], "READY_FOR_READ_ONLY_SEMANTICS_AUDIT")
            result = run_audit(root, config)
            self.assertEqual(result["status"], "PASS_READ_ONLY_SEMANTICS_REVIEW_READY")
            self.assertTrue(result["input_state_unchanged"])
            self.assertFalse(result["training_authorized"])
            self.assertFalse(result["source_asymmetry_is_blocker"])
            for key, old_mtime in source_state.items():
                self.assertEqual(Path(config["paths"][key]).stat().st_mtime_ns, old_mtime)
            source = json.loads((root / "reports" / "news_source_report.json").read_text())
            pairs = source["top_document_domains"]
            self.assertTrue(any(row["collection_source"] == "fnspid_news" and
                                row["domain"] == "benzinga.com"
                                for row in pairs))
            dedup = json.loads((root / "reports" / "news_dedup_report.json").read_text())
            self.assertGreater(dedup["overall"]["url_duplicate_excess_rows"], 0)
            identity = json.loads((root / "reports" / "asset_identity_report.json").read_text())
            self.assertEqual(identity["existing_graph_identity_evidence"]["canonical_buckets"], 0)
            opening = json.loads((root / "reports" / "opening_semantics_report.json").read_text())
            self.assertGreater(opening["corporate_action_overlap"]["metric_rows"], 0)


if __name__ == "__main__":
    unittest.main()

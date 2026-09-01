from __future__ import annotations

import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

import duckdb

from tools.public_information_canonical_lake_v002 import (
    FEATURE_VISIBILITY,
    VERSION,
    audit_build,
    build_plan,
    materialize_bars,
    materialize_news,
    persist_plan,
    validate_config,
)


REPO_ROOT = Path(__file__).resolve().parents[1]
REAL_CONFIG = REPO_ROOT / "config" / "public_information_canonical_lake_v002.json"
REAL_SCHEMA = REPO_ROOT / "database" / "public_information_v002_catalog_schema.sql"


def _sqlite(path: Path, script: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with sqlite3.connect(path) as connection:
        connection.executescript(script)


def _raw_parquets(root: Path) -> tuple[Path, Path]:
    raw = root / "raw"
    raw.mkdir(parents=True)
    bars = raw / "bars.parquet"
    news = raw / "news.parquet"
    with duckdb.connect() as connection:
        connection.execute(
            """
            CREATE TABLE bars(
              ticker VARCHAR,name VARCHAR,timestamp TIMESTAMPTZ,open DOUBLE,
              high DOUBLE,low DOUBLE,close DOUBLE,volume DOUBLE,
              trade_count BIGINT,vol_weighted_avg_price DOUBLE
            )
            """
        )
        connection.executemany(
            "INSERT INTO bars VALUES (?,?,?,?,?,?,?,?,?,?)",
            [
                ("AAPL", "Apple", "2024-01-02 13:00:00+00", 99, 99, 99, 99, 2, 1, 99),
                ("AAPL", "Apple", "2024-01-02 14:30:00+00", 100, 102, 99, 101, 10, 2, 100.5),
                ("AAPL", "Apple", "2024-01-02 14:31:00+00", 101, 103, 100, 102, 20, 3, 101.5),
                ("AAPL", "Apple", "2024-01-02 21:00:00+00", 102, 102, 102, 102, 1, 1, 102),
                ("AAPL", "Apple", "2024-01-03 14:30:00+00", 105, 106, 104, 105, 12, 2, 105),
                ("AAPL", "Apple", "2024-01-03 14:31:00+00", 105, 107, 104, 106, 18, 2, 105.5),
                ("AAPL", "Apple", "2025-01-02 14:30:00+00", 110, 111, 109, 110, 9, 2, 110),
                ("OUT", "Outside", "2024-01-02 14:30:00+00", 10, 11, 9, 10, 5, 1, 10),
            ],
        )
        connection.execute(f"COPY bars TO '{bars.as_posix()}' (FORMAT PARQUET)")
        connection.execute("CREATE TABLE news(date VARCHAR,text VARCHAR,extra_fields VARCHAR)")
        base = {
            "dataset": "fnspid_news",
            "dataset_source": "hf:Zihan1004/FNSPID",
            "publisher": "Benzinga Newsdesk",
            "url": "https://www.benzinga.com/story#fragment",
            "time_precision": "minute",
            "tz_hint": "America/New_York",
        }
        rows = [
            ("2024-01-02 00:00:00", "Apple launches product", json.dumps({**base, "stocks": ["AAPL"]})),
            ("2024-01-02 00:00:00", "Apple launches product", json.dumps({**base, "stocks": ["MSFT"]})),
            ("2024-01-02 15:01:00", "Apple launches product", json.dumps({**base, "stocks": ["AAPL"]})),
            ("2024-01-02 15:02:00", "Apple updates product details", json.dumps({**base, "stocks": ["AAPL"]})),
            ("2024-01-02 15:01:00", "Apple launches product", json.dumps({
                **base, "url": "https://www.reuters.com/syndicated", "publisher": "Reuters",
                "stocks": ["AAPL"],
            })),
            ("2025-01-02 15:01:00", "Apple launches product", json.dumps({
                **base, "stocks": ["AAPL"]
            })),
            ("2024-01-03", "Daily market summary", json.dumps({
                "dataset": "finsen_us_2007_2023", "dataset_source": "test",
                "url": "", "stocks": [], "time_precision": "day",
                "tz_hint": "America/New_York",
            })),
        ]
        connection.executemany("INSERT INTO news VALUES (?,?,?)", rows)
        connection.execute(f"COPY news TO '{news.as_posix()}' (FORMAT PARQUET)")
    return bars, news


def _fixture(root: Path) -> dict:
    bars, news = _raw_parquets(root)
    market, core, graph, intake = (root / name for name in (
        "market.db", "core.db", "graph.db", "intake.db"
    ))
    _sqlite(
        market,
        """
        CREATE TABLE assets(asset_id INTEGER PRIMARY KEY,ticker TEXT);
        INSERT INTO assets VALUES(1,'AAPL'),(2,'MSFT');
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
          (1,'AAPL','Technology','2024-01-04'),
          (2,'MSFT','Technology','2024-01-02'),
          (2,'MSFT','Technology','2024-01-03'),
          (2,'MSFT','Technology','2024-01-04');
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
        intake,
        """
        CREATE TABLE dataset_snapshots(
          snapshot_id TEXT,dataset_key TEXT,profile_name TEXT,resolved_revision TEXT,
          manifest_sha256 TEXT,manifest_path TEXT,selected_file_count INTEGER,
          selected_bytes INTEGER,last_verified_at_utc TEXT
        );
        CREATE TABLE snapshot_files(
          snapshot_id TEXT,repo_path TEXT,size_bytes INTEGER,local_path TEXT,
          status TEXT,local_size_bytes INTEGER
        );
        """,
    )
    with sqlite3.connect(intake) as connection:
        for key, profile, path in (("bars", "full", bars), ("news", "priority", news)):
            digest = (key * 64)[:64]
            manifest = root / f"{key}_manifest.json"
            manifest.write_text(json.dumps({"manifest_sha256": digest}), encoding="utf-8")
            connection.execute(
                "INSERT INTO dataset_snapshots VALUES(?,?,?,?,?,?,?,?,?)",
                (f"s_{key}", key, profile, "rev", digest, str(manifest), 1,
                 path.stat().st_size, "2024-01-04T00:00:00Z"),
            )
            connection.execute(
                "INSERT INTO snapshot_files VALUES(?,?,?,?,?,?)",
                (f"s_{key}", path.name, path.stat().st_size, str(path),
                 "COMPLETE", path.stat().st_size),
            )
    config = json.loads(REAL_CONFIG.read_text(encoding="utf-8"))
    config["paths"] = {
        "intake_catalog_db": str(intake),
        "v002_catalog_db": str(root / "v002_catalog.db"),
        "v002_catalog_schema": str(REAL_SCHEMA),
        "market_db": str(market),
        "core_db": str(core),
        "graph_identity_db": str(graph),
        "lake_root": str(root / "lake_v002"),
        "report_root": str(root / "reports_v002"),
    }
    config["snapshots"] = {
        "bars": {"dataset_key": "bars", "profile_name": "full"},
        "news": {"dataset_key": "news", "profile_name": "priority"},
    }
    config["storage"]["managed_roots"] = [str(root / "raw"), str(root / "lake_v002")]
    config["storage"]["stage_estimate_bytes"] = {"bars": 1, "news": 1}
    config["storage"]["minimum_free_after_operation_bytes"] = 0
    config["duckdb"]["memory_limit"] = "1GB"
    return config


def _scan(path: Path) -> str:
    return f"read_parquet('{(path / '**' / '*.parquet').as_posix()}',hive_partitioning=true)"


class PublicInformationCanonicalLakeV002Tests(unittest.TestCase):
    def test_contract_blocks_training_median_and_impact_t0_feature(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = _fixture(root)
            self.assertTrue(validate_config(root, config)["valid"])
            bad = copy.deepcopy(config)
            bad["training_authorized"] = True
            self.assertFalse(validate_config(root, bad)["valid"])
            bad = copy.deepcopy(config)
            bad["bars"]["cross_source_policy"] = "MEDIAN"
            self.assertFalse(validate_config(root, bad)["valid"])
            bad = copy.deepcopy(config)
            bad["news"]["market_impact_t0_policy"] = "FEATURE"
            self.assertFalse(validate_config(root, bad)["valid"])

    def test_full_synthetic_build_is_idempotent_and_preserves_units(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config = _fixture(root)
            sources = {
                key: Path(config["paths"][key]).stat().st_mtime_ns
                for key in ("intake_catalog_db", "market_db", "core_db", "graph_identity_db")
            }
            plan = build_plan(root, config)
            persisted = persist_plan(root, config, plan)
            self.assertEqual(persisted["status"], "READY_FOR_CANONICAL_MATERIALIZATION")
            bars = materialize_bars(root, config, plan)
            news = materialize_news(root, config, plan)
            result = audit_build(root, config, plan)
            self.assertEqual(result["status"], "PASS_CANONICAL_LAKE_REVIEW_READY")
            self.assertFalse(result["training_authorized"])
            self.assertTrue(bars["input_state_unchanged"])
            self.assertTrue(news["input_state_unchanged"])
            bar_lake = Path(plan["lake_path"]) / "bar_sessions"
            self.assertTrue(any(bar_lake.glob("**/trading_year=2024")))
            self.assertTrue(any(bar_lake.glob("**/trading_year=2025")))
            reused_bars = materialize_bars(root, config, plan)
            self.assertTrue(reused_bars["idempotent_reuse"])
            reused = materialize_news(root, config, plan)
            self.assertTrue(reused["idempotent_reuse"])
            for key, old_mtime in sources.items():
                self.assertEqual(Path(config["paths"][key]).stat().st_mtime_ns, old_mtime)
            lake = Path(plan["lake_path"])
            with duckdb.connect() as connection:
                docs = connection.execute(
                    f"SELECT COUNT(*),COUNT(DISTINCT document_id) FROM {_scan(lake / 'news_document_versions')}"
                ).fetchone()
                self.assertEqual(docs, (4, 3))
                story = connection.execute(
                    f"SELECT MAX(document_versions) FROM {_scan(lake / 'news_story_candidates')}"
                ).fetchone()[0]
                self.assertGreaterEqual(story, 2)
                links = connection.execute(
                    f"SELECT COUNT(*),COUNT(DISTINCT source_ticker) FROM {_scan(lake / 'news_asset_links')}"
                ).fetchone()
                self.assertEqual(links[1], 2)
                impact_status = connection.execute(
                    f"SELECT DISTINCT market_impact_t0_status FROM {_scan(lake / 'information_episode_candidates')}"
                ).fetchall()
                self.assertEqual(impact_status, [("NOT_INFERRED_OUTCOME_SIDE_ONLY",)])
                policies = connection.execute(
                    f"SELECT DISTINCT cross_source_policy FROM {_scan(lake / 'bar_source_reconciliation')}"
                ).fetchall()
                self.assertEqual(policies, [("PRESERVE_BOTH_NO_MEDIAN_NO_OVERWRITE",)])
            midnight = json.loads(
                (Path(plan["report_path"]) / "midnight_forensics_report.json").read_text()
            )
            self.assertEqual(
                midnight["conflicting_clock_documents"]["documents_with_midnight_and_exact_evidence"], 1
            )
            clock = json.loads((Path(plan["report_path"]) / "causal_clock_report.json").read_text())
            self.assertFalse(clock["publication_equals_market_impact_t0"])
            self.assertFalse(clock["future_document_can_be_earlier_feature"])


if __name__ == "__main__":
    unittest.main()

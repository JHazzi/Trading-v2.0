import copy
import json
import sqlite3
import tempfile
import unittest
from contextlib import closing
from pathlib import Path

from tools.information_integration_readiness_v001 import (
    build_reports,
    file_state,
    validate_config,
)


ROOT = Path(__file__).resolve().parents[1]
BASE_CONFIG = json.loads(
    (ROOT / "config" / "information_integration_readiness_v001.json").read_text(
        encoding="utf-8"
    )
)


def create_core(path: Path, config: dict) -> None:
    features = []
    for block in config["feature_blocks"].values():
        if block["source"] == "reference_state":
            features.extend(block["features"])
    feature_sql = ",".join(f'"{name}" REAL' for name in features)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            f"""
            CREATE TABLE market_daily_v003_states(
                state_id TEXT PRIMARY KEY,
                asset_id INTEGER NOT NULL,
                ticker TEXT NOT NULL,
                sector TEXT,
                trading_day TEXT NOT NULL,
                state_time TEXT NOT NULL,
                feature_version TEXT NOT NULL,
                state_point_in_time_verified INTEGER NOT NULL,
                own_history_days INTEGER NOT NULL,
                {feature_sql},
                UNIQUE(asset_id,trading_day)
            )
            """
        )
        conn.execute("CREATE TABLE build_metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL)")
        conn.execute(
            "INSERT INTO build_metadata VALUES (?,?)",
            (
                "config",
                json.dumps(
                    {
                        "source_asof_mode": "historical_session_close_assumption",
                        "state_clock": "exchange_session_close",
                        "strict_historical_pit": False,
                    }
                ),
            ),
        )
        columns = [
            "state_id",
            "asset_id",
            "ticker",
            "sector",
            "trading_day",
            "state_time",
            "feature_version",
            "state_point_in_time_verified",
            "own_history_days",
            *features,
        ]
        placeholders = ",".join("?" for _ in columns)
        for index, day in enumerate(("2026-08-20", "2026-08-21"), start=1):
            row = [
                f"s{index}",
                1,
                "AAA",
                "Technology",
                day,
                f"{day}T20:00:00+00:00",
                "market_daily_state_v003_core",
                0,
                300,
                *([1.0] * len(features)),
            ]
            conn.execute(
                f"INSERT INTO market_daily_v003_states({','.join(map(lambda x: chr(34)+x+chr(34), columns))}) VALUES ({placeholders})",
                row,
            )


def create_market(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE assets(asset_id INTEGER PRIMARY KEY,ticker TEXT,name TEXT,asset_type TEXT,sector TEXT,industry TEXT,country TEXT,currency TEXT,exchange TEXT,active INTEGER,source TEXT);
            CREATE TABLE ingestion_sources(source_id TEXT PRIMARY KEY,source_name TEXT,source_type TEXT);
            CREATE TABLE price_bar_versions(price_bar_version_id TEXT PRIMARY KEY,source_id TEXT,asset_id INTEGER,interval TEXT,trading_day TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL,adjusted_close REAL);
            CREATE TABLE price_bar_observations(price_observation_id TEXT PRIMARY KEY,asset_id INTEGER,trading_day TEXT,availability_basis TEXT,point_in_time_verified INTEGER);
            CREATE TABLE price_bars(price_bar_id INTEGER PRIMARY KEY,asset_id INTEGER,timestamp TEXT,interval TEXT,source TEXT,trading_day TEXT,open REAL,high REAL,low REAL,close REAL,volume REAL);
            CREATE TABLE market_state_v002_snapshots(snapshot_id INTEGER PRIMARY KEY,asset_id INTEGER,timestamp TEXT,feature_version TEXT);
            CREATE TABLE news_documents(news_id TEXT PRIMARY KEY,published_at TEXT,ingested_at TEXT,source_name TEXT,title TEXT,summary TEXT,raw_text TEXT,source_provider TEXT);
            CREATE TABLE news_assets(news_id TEXT,asset_id INTEGER,PRIMARY KEY(news_id,asset_id));
            CREATE TABLE news_features(news_id TEXT PRIMARY KEY);
            CREATE TABLE event_news(event_id TEXT,news_id TEXT,PRIMARY KEY(event_id,news_id));
            CREATE TABLE event_cluster_news(cluster_id TEXT,news_id TEXT,PRIMARY KEY(cluster_id,news_id));
            CREATE TABLE event_clusters(cluster_id TEXT PRIMARY KEY);
            CREATE TABLE raw_source_documents(raw_document_id TEXT PRIMARY KEY,source_id TEXT,published_at TEXT,available_at TEXT,retrieved_at TEXT,parser_status TEXT);
            CREATE TABLE sec_filings(raw_document_id TEXT PRIMARY KEY,form TEXT,ticker_at_ingestion TEXT,cik TEXT,acceptance_datetime TEXT);
            CREATE TABLE normalized_event_state_snapshots(event_state_id TEXT PRIMARY KEY,event_id TEXT,asset_id INTEGER,state_time TEXT,feature_version TEXT,event_type TEXT,point_in_time_evidence_fraction REAL);
            CREATE TABLE normalized_event_observations(event_observation_id TEXT PRIMARY KEY,availability_is_point_in_time INTEGER,available_at TEXT);
            CREATE TABLE macro_observations(macro_observation_id INTEGER PRIMARY KEY,symbol TEXT,source TEXT,observation_time TEXT);
            """
        )
        conn.execute(
            "INSERT INTO assets VALUES (1,'AAA','A','equity','Technology','Software','US','USD','XNYS',1,'unit')"
        )
        conn.execute("INSERT INTO ingestion_sources VALUES ('y','Yahoo','market_data_aggregator')")
        conn.execute("INSERT INTO ingestion_sources VALUES ('sec','SEC EDGAR','regulator')")
        for index, day in enumerate(("2026-08-20", "2026-08-21"), start=1):
            conn.execute(
                "INSERT INTO price_bar_versions VALUES (?,?,?,?,?,?,?,?,?,?,?)",
                (f"v{index}", "y", 1, "1d", day, 10, 11, 9, 10.5, 1000, 10.5),
            )
            conn.execute(
                "INSERT INTO price_bar_observations VALUES (?,?,?,?,?)",
                (f"o{index}", 1, day, "session_close_backfill_assumption", 0),
            )
        conn.execute(
            "INSERT INTO price_bars VALUES (1,1,'2026-08-21T14:30:00+00:00','1m','legacy:yfinance','2026-08-21',10,11,9,10.5,100)"
        )
        conn.execute(
            "INSERT INTO market_state_v002_snapshots VALUES (1,1,'2026-08-21T14:30:00+00:00','market_state_v0.2.0')"
        )
        conn.execute(
            "INSERT INTO news_documents VALUES ('n1','2026-08-20T12:00:00+00:00','2026-08-22','Reuters','title','summary',NULL,NULL)"
        )
        conn.execute("INSERT INTO news_assets VALUES ('n1',1)")
        conn.execute(
            "INSERT INTO raw_source_documents VALUES ('r1','sec','2026-08-20T10:00:00+00:00','2026-08-20T10:00:00+00:00','2026-08-22T10:00:00+00:00','parsed')"
        )
        conn.execute(
            "INSERT INTO sec_filings VALUES ('r1','8-K','AAA','1','2026-08-20T10:00:00+00:00')"
        )
        conn.execute(
            "INSERT INTO normalized_event_state_snapshots VALUES ('es1','e1',1,'2026-08-20T10:00:00+00:00','event_state_v0031_deep','results',0.0)"
        )
        conn.execute(
            "INSERT INTO normalized_event_observations VALUES ('eo1',0,'2026-08-20T10:00:00+00:00')"
        )
        conn.execute("INSERT INTO macro_observations VALUES (1,'VIX','legacy','2026-08-20')")


def create_day_context(path: Path, table: str, features: list[str], financial: bool) -> None:
    semantics = (
        "historical_strict_pit INTEGER,price_observation_policy TEXT,action_observation_policy TEXT,return_convention TEXT,cash_action_availability_basis TEXT,vix_feature_lag_sessions INTEGER,adjusted_close_used INTEGER"
        if financial
        else "point_in_time_verified INTEGER,availability_basis TEXT"
    )
    feature_sql = ",".join(f'"{name}" REAL' for name in features)
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.execute(
            f'CREATE TABLE "{table}"(trading_day TEXT PRIMARY KEY,{feature_sql},feature_version TEXT,{semantics})'
        )
        columns = ["trading_day", *features, "feature_version"]
        if financial:
            columns.extend(
                [
                    "historical_strict_pit",
                    "price_observation_policy",
                    "action_observation_policy",
                    "return_convention",
                    "cash_action_availability_basis",
                    "vix_feature_lag_sessions",
                    "adjusted_close_used",
                ]
            )
            tail = [0, "asof", "asof", "total_return", "effective_day", 1, 0]
        else:
            columns.extend(["point_in_time_verified", "availability_basis"])
            tail = [0, "historical_session_close_assumption"]
        for day in ("2026-08-20", "2026-08-21"):
            values = [day, *([1.0] * len(features)), "unit_v1", *tail]
            conn.execute(
                f'INSERT INTO "{table}"({",".join(chr(34)+x+chr(34) for x in columns)}) VALUES ({",".join("?" for _ in values)})',
                values,
            )


def create_event(path: Path) -> None:
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE samples(sample_id TEXT PRIMARY KEY,asset_id INTEGER,ticker TEXT,origin_day TEXT,origin_time TEXT,delay_seconds INTEGER,strict_pit INTEGER,event_features_json TEXT,market_features_json TEXT);
            CREATE TABLE sample_events(sample_id TEXT,event_id TEXT,event_state_id TEXT,accession TEXT,PRIMARY KEY(sample_id,event_id));
            CREATE TABLE sample_groups(sample_id TEXT,group_kind TEXT,group_id TEXT,PRIMARY KEY(sample_id,group_kind,group_id));
            CREATE TABLE outcomes(sample_id TEXT,horizon_sessions INTEGER,status TEXT,origin_day TEXT,PRIMARY KEY(sample_id,horizon_sessions));
            """
        )
        conn.execute(
            "INSERT INTO samples VALUES ('x',1,'AAA','2026-08-20','2026-08-20T20:00:00+00:00',0,0,?,?)",
            (json.dumps({"event_type": 1}), json.dumps({"asset_vol_63d_pct": 1})),
        )
        conn.execute("INSERT INTO sample_events VALUES ('x','e','es','a')")
        conn.execute("INSERT INTO sample_groups VALUES ('x','event','e')")
        conn.execute("INSERT INTO outcomes VALUES ('x',1,'usable','2026-08-20')")


def create_information(path: Path, *, bad_clock: bool = False) -> None:
    with closing(sqlite3.connect(path)) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE source_observations(observation_id TEXT PRIMARY KEY,source_type TEXT,source_name TEXT,strict_pit INTEGER,available_at TEXT,retrieved_at TEXT);
            CREATE TABLE expectation_observations(observation_id TEXT PRIMARY KEY,asset_ticker TEXT,entity_key TEXT,source_observation_id TEXT,available_at TEXT,provider_as_of TEXT,strict_pit INTEGER,expectation_type TEXT,metric_key TEXT,statistic_key TEXT);
            CREATE TABLE scheduled_event_window_observations(observation_id TEXT PRIMARY KEY,asset_ticker TEXT,source_observation_id TEXT,available_at TEXT,scheduled_date TEXT,strict_pit INTEGER,event_type TEXT,event_status TEXT,time_precision TEXT);
            CREATE TABLE scheduled_event_observations(observation_id TEXT PRIMARY KEY);
            CREATE TABLE economic_fact_observations(observation_id TEXT PRIMARY KEY);
            CREATE TABLE news_document_observations(observation_id TEXT PRIMARY KEY);
            CREATE TABLE news_asset_annotations(observation_id TEXT PRIMARY KEY);
            CREATE TABLE news_story_cluster_candidates(observation_id TEXT PRIMARY KEY);
            """
        )
        available = "2026-08-28T10:00:00+00:00"
        retrieved = "2026-08-27T10:00:00+00:00" if bad_clock else available
        conn.execute(
            "INSERT INTO source_observations VALUES ('src','analyst_expectation_feed','Alpha Vantage',1,?,?)",
            (available, retrieved),
        )
        conn.execute(
            "INSERT INTO expectation_observations VALUES ('exp','AAA','AAA','src',?,NULL,1,'analyst_consensus','eps','average')",
            (available,),
        )
        conn.execute(
            "INSERT INTO scheduled_event_window_observations VALUES ('sch','AAA','src',?,'2026-10-01',1,'earnings_report','scheduled','date_only')",
            (available,),
        )


def create_graph(entity_path: Path, relation_path: Path) -> None:
    with closing(sqlite3.connect(entity_path)) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE registry_runs(registry_run_id TEXT PRIMARY KEY);
            CREATE TABLE identity_evidence_buckets(identity_bucket_id TEXT PRIMARY KEY,registrant_asset_id INTEGER,normalized_legal_name TEXT,identity_status TEXT,jurisdiction_status TEXT,evidence_occurrence_count INTEGER,first_evidence_available_at TEXT,last_evidence_available_at TEXT);
            CREATE TABLE identity_alias_evidence(alias_evidence_id TEXT PRIMARY KEY);
            INSERT INTO registry_runs VALUES ('r');
            INSERT INTO identity_evidence_buckets VALUES ('b',1,'sub','evidence_bucket_not_canonical','observed',1,'2020-01-01','2026-01-01');
            """
        )
    with closing(sqlite3.connect(relation_path)) as conn, conn:
        conn.executescript(
            """
            CREATE TABLE extraction_runs(extraction_run_id TEXT PRIMARY KEY);
            CREATE TABLE evidence_claims(evidence_claim_id TEXT PRIMARY KEY,registrant_asset_id INTEGER,resolved_named_entity_id INTEGER,edge_ready INTEGER,availability_is_point_in_time INTEGER,evidence_available_at TEXT,claim_kind TEXT,resolution_status TEXT);
            CREATE TABLE contract_party_sets(party_set_id TEXT PRIMARY KEY);
            INSERT INTO extraction_runs VALUES ('r');
            INSERT INTO evidence_claims VALUES ('c',1,NULL,0,0,'2020-01-01','reported_subsidiary_of_registrant','unresolved');
            """
        )


def create_fixture(root: Path, *, bad_clock: bool = False, missing_external_day: bool = False) -> tuple[dict, Path]:
    config = copy.deepcopy(BASE_CONFIG)
    paths = {
        "reference_state": root / "core.db",
        "market_source": root / "market.db",
        "external_market": root / "external.db",
        "financial_conditions": root / "financial.db",
        "historical_event_dataset": root / "event.db",
        "prospective_information": root / "information.db",
        "graph_entity_evidence": root / "graph_entities.db",
        "graph_relation_evidence": root / "graph_relations.db",
    }
    config["reference_state"]["database"] = str(paths["reference_state"])
    for name in config["sources"]:
        config["sources"][name]["database"] = str(paths[name])

    create_core(paths["reference_state"], config)
    create_market(paths["market_source"])
    create_day_context(
        paths["external_market"],
        config["sources"]["external_market"]["table"],
        config["feature_blocks"]["broad_market_state"]["features"],
        False,
    )
    if missing_external_day:
        with closing(sqlite3.connect(paths["external_market"])) as conn, conn:
            conn.execute("DELETE FROM market_external_state_v005 WHERE trading_day='2026-08-21'")
    create_day_context(
        paths["financial_conditions"],
        config["sources"]["financial_conditions"]["table"],
        config["feature_blocks"]["financial_conditions_state"]["features"],
        True,
    )
    create_event(paths["historical_event_dataset"])
    create_information(paths["prospective_information"], bad_clock=bad_clock)
    create_graph(paths["graph_entity_evidence"], paths["graph_relation_evidence"])
    config_path = root / "config.json"
    config_path.write_text(json.dumps(config), encoding="utf-8")
    return config, config_path


class InformationIntegrationReadinessV001Tests(unittest.TestCase):
    def test_valid_config_rejects_no_current_features(self) -> None:
        result = validate_config(copy.deepcopy(BASE_CONFIG))
        self.assertTrue(result["valid"], result["errors"])
        self.assertEqual(result["feature_leakage_hits"], [])

    def test_config_rejects_forbidden_database(self) -> None:
        payload = copy.deepcopy(BASE_CONFIG)
        payload["sources"]["market_source"]["database"] = (
            "data/processed/market_brain_v009_prospective.db"
        )
        result = validate_config(payload)
        self.assertFalse(result["valid"])
        self.assertTrue(result["forbidden_path_hits"])

    def test_config_rejects_outcome_feature(self) -> None:
        payload = copy.deepcopy(BASE_CONFIG)
        payload["feature_blocks"]["own_state"]["features"].append("total_return_pct")
        result = validate_config(payload)
        self.assertFalse(result["valid"])
        self.assertIn("total_return_pct", result["feature_leakage_hits"])

    def test_full_fixture_passes_read_only_and_keeps_training_blocked(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, config_path = create_fixture(root)
            before = {
                path.name: file_state(path)
                for path in root.glob("*.db")
            }
            reports = build_reports(
                root, config_path, root / "reports", write_outputs=False
            )
            after = {
                path.name: file_state(path)
                for path in root.glob("*.db")
            }
            self.assertEqual(before, after)
            self.assertEqual(
                reports["audit"]["status"],
                "PASS_READ_ONLY_INFORMATION_INVENTORY_CONTEXT_PLAN_READY",
            )
            self.assertFalse(reports["audit"]["training_authorized"])
            self.assertFalse(reports["plan"]["guards"]["training_authorized"])
            self.assertEqual(reports["audit"]["forbidden_open_hits"], [])
            self.assertEqual(
                reports["inventory"]["layers"]["prospective_information"][
                    "feature_eligibility"
                ],
                "PROSPECTIVE_ACCUMULATION_ONLY_NOT_HISTORICAL_BACKFILL",
            )
            self.assertEqual(
                reports["inventory"]["layers"]["market_core"]["asset_coverage"][0][
                    "ticker"
                ],
                "AAA",
            )
            self.assertEqual(
                reports["inventory"]["layers"]["market_source"]["assets"][
                    "catalog"
                ][0]["daily_sessions"],
                2,
            )
            self.assertEqual(
                reports["inventory"]["layers"]["graph_evidence"][
                    "registrant_asset_catalog"
                ][0]["ticker"],
                "AAA",
            )

    def test_missing_context_day_fails_exact_coverage_gate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, config_path = create_fixture(root, missing_external_day=True)
            reports = build_reports(
                root, config_path, root / "reports", write_outputs=False
            )
            self.assertFalse(
                reports["audit"]["hard_gates"][
                    "external_context_exact_core_coverage"
                ]
            )
            self.assertTrue(reports["audit"]["status"].startswith("REVIEW_"))

    def test_null_inside_core_context_domain_fails_exact_coverage_gate(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, config_path = create_fixture(root)
            with closing(sqlite3.connect(root / "external.db")) as conn, conn:
                conn.execute(
                    "UPDATE market_external_state_v005 SET spy_return_1d_pct=NULL "
                    "WHERE trading_day='2026-08-20'"
                )
            reports = build_reports(
                root, config_path, root / "reports", write_outputs=False
            )
            self.assertEqual(
                reports["inventory"]["layers"]["external_market"][
                    "core_join_feature_null_counts"
                ]["spy_return_1d_pct"],
                1,
            )
            self.assertFalse(
                reports["audit"]["hard_gates"][
                    "external_context_exact_core_coverage"
                ]
            )

    def test_strict_pit_available_after_retrieval_fails(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, config_path = create_fixture(root, bad_clock=True)
            reports = build_reports(
                root, config_path, root / "reports", write_outputs=False
            )
            self.assertFalse(
                reports["audit"]["hard_gates"][
                    "strict_pit_capture_source_clocks_valid"
                ]
            )
            self.assertTrue(reports["audit"]["status"].startswith("REVIEW_"))

    def test_missing_source_is_review_not_zero_data(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            config, config_path = create_fixture(root)
            missing = root / "missing.db"
            config["sources"]["graph_relation_evidence"]["database"] = str(missing)
            config_path.write_text(json.dumps(config), encoding="utf-8")
            reports = build_reports(
                root, config_path, root / "reports", write_outputs=False
            )
            self.assertTrue(reports["audit"]["status"].startswith("REVIEW_"))
            self.assertFalse(
                reports["audit"]["hard_gates"][
                    "all_required_sources_and_tables_present"
                ]
            )

    def test_outputs_are_idempotent_and_hashed(self) -> None:
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, config_path = create_fixture(root)
            output = root / "reports"
            first = build_reports(root, config_path, output, write_outputs=True)
            second = build_reports(root, config_path, output, write_outputs=True)
            self.assertTrue((output / "audit.json").exists())
            self.assertTrue((output / "INFORMATION_INVENTORY.md").exists())
            self.assertEqual(
                first["inventory"]["layers"]["market_core"]["summary"],
                second["inventory"]["layers"]["market_core"]["summary"],
            )
            audit = json.loads((output / "audit.json").read_text(encoding="utf-8"))
            self.assertIn("inventory", audit["outputs"])


if __name__ == "__main__":
    unittest.main()

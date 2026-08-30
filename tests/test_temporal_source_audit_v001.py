import copy
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.temporal_source_audit_v001 import build_report, validate_temporal_config


VALID_CONFIG = {
    "version": "market_temporal_dataset_v001",
    "status": "materializer_ready_full_run_pending_no_training",
    "horizon_contract": {
        "tau_domain": {
            "minimum_sessions": 1,
            "maximum_sessions": 252,
            "unit": "eligible_exchange_sessions",
            "integer_only": True,
        },
        "default_materialization_strategy": "configured_sparse",
        "supported_materialization_strategies": [
            "configured_sparse", "configured_plus", "dense_all"
        ],
        "existing_evaluation_sessions": [1, 3, 5, 10],
        "training_anchor_sessions": [1, 2, 3, 5, 8, 10, 13, 21, 34, 63, 126, 252],
        "temporal_generalization_holdout_sessions": [7, 17, 42, 90, 180],
        "materialized_sessions": [1, 2, 3, 5, 7, 8, 10, 13, 17, 21, 34, 42, 63, 90, 126, 180, 252],
        "maximum_sessions": 252,
    },
    "guards": {
        "training_authorized": False,
        "v009_artifacts_loaded_or_modified": False,
        "source_market_db_mutation_allowed": False,
        "market_v003_core_mutation_allowed": False,
    },
}


def make_source(path: Path) -> None:
    with sqlite3.connect(path) as conn:
        conn.executescript(
            """
            CREATE TABLE assets (
                asset_id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL UNIQUE
            );
            CREATE TABLE price_bars (
                price_bar_id INTEGER PRIMARY KEY,
                asset_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                interval TEXT NOT NULL,
                open REAL NOT NULL,
                high REAL NOT NULL,
                low REAL NOT NULL,
                close REAL NOT NULL,
                source TEXT NOT NULL,
                is_adjusted INTEGER NOT NULL DEFAULT 0,
                session_id TEXT,
                trading_day TEXT
            );
            CREATE INDEX idx_price_bars_asset_time
                ON price_bars(asset_id, timestamp);
            CREATE TABLE market_sessions (
                session_id TEXT PRIMARY KEY,
                trading_day TEXT NOT NULL,
                exchange TEXT NOT NULL,
                session_type TEXT NOT NULL,
                open_time TEXT NOT NULL,
                close_time TEXT NOT NULL
            );
            CREATE TABLE corporate_action_versions (
                version_id INTEGER PRIMARY KEY,
                asset_id INTEGER NOT NULL
            );
            CREATE TABLE corporate_action_observations (
                observation_id INTEGER PRIMARY KEY,
                asset_id INTEGER NOT NULL
            );
            CREATE TABLE market_state_v002_snapshots (
                snapshot_id INTEGER PRIMARY KEY,
                asset_id INTEGER NOT NULL,
                timestamp TEXT NOT NULL,
                last_price REAL NOT NULL,
                feature_version TEXT NOT NULL
            );
            CREATE TABLE realized_outcomes (
                outcome_id INTEGER PRIMARY KEY,
                asset_id INTEGER NOT NULL,
                origin_time TEXT NOT NULL,
                horizon_value INTEGER,
                horizon_unit TEXT,
                horizon_scope TEXT,
                return_pct REAL,
                target_version TEXT
            );
            """
        )
        conn.execute("INSERT INTO assets VALUES (1,'AAA')")
        conn.execute(
            """
            INSERT INTO price_bars(
                price_bar_id,asset_id,timestamp,interval,open,high,low,close,
                source,is_adjusted,session_id,trading_day
            ) VALUES (1,1,'2026-08-28T20:00:00+00:00','1d',10,11,9,10.5,
                      'unit',0,'S1','2026-08-28')
            """
        )
        conn.execute(
            "INSERT INTO market_sessions VALUES "
            "('S1','2026-08-28','XNYS','regular','13:30','20:00')"
        )
        conn.execute("INSERT INTO corporate_action_versions VALUES (1,1)")
        conn.execute("INSERT INTO corporate_action_observations VALUES (1,1)")
        conn.execute(
            "INSERT INTO market_state_v002_snapshots VALUES "
            "(1,1,'2026-08-28T19:00:00+00:00',10.4,'market_state_v002')"
        )
        conn.execute(
            "INSERT INTO realized_outcomes VALUES "
            "(1,1,'2026-08-28T19:00:00+00:00',60,'bar','intrasession',0.2,'t')"
        )


def make_core(path: Path) -> None:
    config = {
        "source_asof_contract": "daily_price_asof_v1",
        "source_asof_mode": "historical_session_close_assumption",
        "state_clock": "exchange_session_close",
        "strict_historical_pit": False,
        "feature_version": "market_daily_state_v003_core",
        "label_version": "market_daily_reaction_v003_core",
        "target": "raw_close_t_to_raw_close_t_plus_h",
    }
    with sqlite3.connect(path) as conn:
        conn.execute(
            "CREATE TABLE build_metadata (key TEXT PRIMARY KEY, value_json TEXT)"
        )
        conn.execute(
            "INSERT INTO build_metadata VALUES (?,?)",
            ("config", json.dumps(config)),
        )
        conn.execute(
            "INSERT INTO build_metadata VALUES (?,?)",
            ("source_db", json.dumps("/tmp/market_data_v2.db")),
        )


class TemporalSourceAuditV001Tests(unittest.TestCase):
    def test_valid_config_has_disjoint_holdouts(self):
        result = validate_temporal_config(copy.deepcopy(VALID_CONFIG))
        self.assertTrue(result["valid"])
        self.assertEqual(result["errors"], [])
        self.assertEqual(result["tau_domain"]["maximum_sessions"], 252)
        self.assertEqual(result["default_materialization_strategy"], "configured_sparse")

    def test_config_rejects_holdout_leakage(self):
        payload = copy.deepcopy(VALID_CONFIG)
        payload["horizon_contract"]["temporal_generalization_holdout_sessions"][0] = 21
        payload["horizon_contract"]["materialized_sessions"] = sorted(
            set(payload["horizon_contract"]["training_anchor_sessions"])
            | set(payload["horizon_contract"]["temporal_generalization_holdout_sessions"])
        )
        result = validate_temporal_config(payload)
        self.assertFalse(result["valid"])
        self.assertTrue(
            any("overlap" in error for error in result["errors"]),
            result["errors"],
        )

    def test_build_report_ready_and_read_only(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "market_data_v2.db"
            core = root / "market_daily_v003_core.db"
            config_path = root / "temporal_dataset_v001.json"
            make_source(source)
            make_core(core)
            config_path.write_text(json.dumps(VALID_CONFIG), encoding="utf-8")

            before_source = source.stat().st_size
            before_core = core.stat().st_size
            report = build_report(source, core, config_path)

            self.assertEqual(report["status"], "READY_FOR_SOURCE_SCHEMA_REVIEW")
            self.assertTrue(report["read_only"])
            self.assertTrue(report["core"]["contract_matches"])
            self.assertTrue(
                all(report["source"]["required_tables"].values()),
                report["source"]["required_tables"],
            )
            self.assertEqual(
                report["source"]["recent_price_bar_probe"]["interval_counts"],
                {"1d": 1},
            )
            self.assertFalse(report["next_gate"]["training_authorized"])
            self.assertFalse(report["next_gate"]["materialization_authorized_by_this_audit"])
            self.assertEqual(before_source, source.stat().st_size)
            self.assertEqual(before_core, core.stat().st_size)

    def test_missing_source_requires_review(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source = root / "missing.db"
            core = root / "core.db"
            config_path = root / "config.json"
            make_core(core)
            config_path.write_text(json.dumps(VALID_CONFIG), encoding="utf-8")
            report = build_report(source, core, config_path)
            self.assertEqual(report["status"], "REVIEW_REQUIRED")
            self.assertTrue(
                any("source DB is missing" in r for r in report["review_reasons"])
            )


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import hashlib
import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tools.temporal_dataset_v001 import (
    BuildBlocked,
    audit_output,
    materialize,
    parse_tau_spec,
    require_training_authorized,
    resolve_taus,
)


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def contract() -> dict:
    return {
        "version": "market_temporal_dataset_v001",
        "status": "materializer_ready_no_training",
        "source_contract": {
            "canonical_market_db": "data/database/market_data_v2.db",
            "market_core_db": "data/processed/market_daily_v003_core.db",
            "source_asof_contract": "daily_price_asof_v1",
            "source_asof_mode": "historical_session_close_assumption",
            "state_clock": "exchange_session_close",
            "strict_historical_pit": False,
            "market_feature_version": "market_daily_state_v003_core",
            "existing_label_version": "market_daily_reaction_v003_core",
            "existing_target": "raw_close_t_to_raw_close_t_plus_h",
        },
        "horizon_contract": {
            "tau_domain": {
                "minimum_sessions": 1, "maximum_sessions": 252,
                "unit": "eligible_exchange_sessions", "integer_only": True,
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
        "raw_close_label_contract": {
            "formula": "100 * (raw_close_target / raw_close_origin - 1)",
            "corporate_action_policy": "exclude_any_present_action_in_open_closed_horizon",
            "provider_adjusted_close_allowed_as_silent_substitute": False,
        },
        "parity_gate": {
            "required_horizons_sessions": [1, 3, 5, 10],
            "compare_fields": [
                "target_trading_day", "return_pct", "corporate_action_overlap",
                "label_status",
            ],
            "return_absolute_tolerance": 1e-9,
            "require_zero_missing_reference_rows": True,
            "training_blocked_if_parity_fails": True,
        },
        "corporate_action_gate": {
            "audit_horizons_sessions": [21, 63, 126, 252],
            "reason": "audit recurring action selection",
            "if_overlap_is_material": "define causal total return target",
        },
        "materialization_contract": {
            "output_db": "data/processed/market_temporal_v001.db",
            "dataset_contract": "market_temporal_horizon_conditioned_outcomes_v001",
            "label_version": "market_temporal_terminal_return_v001",
            "source_and_core_are_read_only": True,
            "idempotent_rebuild": True,
        },
        "guards": {
            "training_authorized": False,
            "v009_artifacts_loaded_or_modified": False,
            "v009_fit_used": False,
            "source_market_db_mutation_allowed": False,
            "market_v003_core_mutation_allowed": False,
            "event_features_allowed": False,
            "graph_features_allowed": False,
            "random_split_allowed": False,
        },
    }


def make_source(path: Path, days: int = 270) -> list[str]:
    day_values = [f"2025-{1 + i // 28:02d}-{1 + i % 28:02d}" for i in range(days)]
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE assets(asset_id INTEGER PRIMARY KEY,ticker TEXT,sector TEXT,
          asset_type TEXT,active INTEGER);
        CREATE TABLE daily_price_asof_configs(
          asof_contract_version TEXT,mode TEXT,cutoff_column TEXT,
          selection_point_in_time_verified INTEGER);
        CREATE TABLE price_observations(
          price_observation_id TEXT PRIMARY KEY,asset_id INTEGER,trading_day TEXT,
          bar_end_utc TEXT,interval TEXT,close REAL,observation_sequence INTEGER,
          observed_at TEXT,causal_available_at TEXT);
        CREATE VIEW daily_price_quality_gated_observations_v002 AS
          SELECT * FROM price_observations;
        CREATE TABLE corporate_action_versions(
          corporate_action_version_id TEXT PRIMARY KEY,is_present INTEGER);
        CREATE TABLE corporate_action_observations(
          action_observation_id TEXT PRIMARY KEY,corporate_action_version_id TEXT,
          asset_id INTEGER,effective_trading_day TEXT,action_type TEXT,
          observation_sequence INTEGER,observed_at TEXT);
        """)
        conn.execute("INSERT INTO daily_price_asof_configs VALUES(?,?,?,?)", (
            "daily_price_asof_v1", "historical_session_close_assumption",
            "available_at", 0,
        ))
        for asset_id in (1, 2):
            conn.execute("INSERT INTO assets VALUES(?,?,?,?,?)", (
                asset_id, f"T{asset_id}", "Tech" if asset_id == 1 else "Bank",
                "equity", 1,
            ))
            for index, day in enumerate(day_values):
                close = 100.0 + asset_id + index * 0.1
                conn.execute(
                    "INSERT INTO price_observations VALUES(?,?,?,?,?,?,?,?,?)",
                    (f"p{asset_id}_{index}", asset_id, day, day + "T21:00:00+00:00",
                     "1d", close, 1, "2026-01-01T00:00:00+00:00",
                     day + "T21:00:00+00:00"),
                )
        conn.execute("INSERT INTO corporate_action_versions VALUES('v1',1)")
        conn.execute(
            "INSERT INTO corporate_action_observations VALUES(?,?,?,?,?,?,?)",
            ("a1", "v1", 1, day_values[30], "dividend", 1,
             "2026-01-01T00:00:00+00:00"),
        )
    return day_values


def make_core(path: Path, days: list[str], corrupt: bool = False) -> None:
    cfg = {
        "source_asof_contract": "daily_price_asof_v1",
        "source_asof_mode": "historical_session_close_assumption",
        "state_clock": "exchange_session_close",
        "strict_historical_pit": False,
        "feature_version": "market_daily_state_v003_core",
        "label_version": "market_daily_reaction_v003_core",
        "target": "raw_close_t_to_raw_close_t_plus_h",
    }
    state_positions = (5, 20, 50, 200, 265)
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE build_metadata(key TEXT PRIMARY KEY,value_json TEXT);
        CREATE TABLE market_daily_v003_states(
          state_id TEXT PRIMARY KEY,asset_id INTEGER,ticker TEXT,sector TEXT,
          trading_day TEXT,state_time TEXT,feature_version TEXT);
        CREATE TABLE market_daily_v003_labels(
          label_id TEXT PRIMARY KEY,state_id TEXT,asset_id INTEGER,
          origin_trading_day TEXT,target_trading_day TEXT,horizon_sessions INTEGER,
          return_pct REAL,corporate_action_overlap INTEGER,label_status TEXT,
          label_version TEXT);
        """)
        metadata = {
            "config": cfg, "source_db": "/tmp/market_data_v2.db",
            "states": len(state_positions) * 2, "state_assets": 2,
            "state_first_day": days[min(state_positions)],
            "state_last_day": days[-1], "labels": len(state_positions) * 8,
        }
        for key, value in metadata.items():
            conn.execute("INSERT INTO build_metadata VALUES(?,?)", (
                key, json.dumps(value),
            ))
        action_day = days[30]
        for asset_id in (1, 2):
            ticker, sector = f"T{asset_id}", "Tech" if asset_id == 1 else "Bank"
            for pos in state_positions:
                state_id = f"s{asset_id}_{pos}"
                conn.execute(
                    "INSERT INTO market_daily_v003_states VALUES(?,?,?,?,?,?,?)",
                    (state_id, asset_id, ticker, sector, days[pos],
                     days[pos] + "T21:00:00+00:00", "market_daily_state_v003_core"),
                )
                for horizon in (1, 3, 5, 10):
                    target_pos = pos + horizon
                    if target_pos >= len(days):
                        target_day, value, overlap, status = None, None, 0, "insufficient_future"
                    else:
                        target_day = days[target_pos]
                        origin_close = 100.0 + asset_id + pos * 0.1
                        target_close = 100.0 + asset_id + target_pos * 0.1
                        value = 100.0 * (target_close / origin_close - 1.0)
                        overlap = int(asset_id == 1 and days[pos] < action_day <= target_day)
                        status = "corporate_action_overlap" if overlap else "usable"
                    if corrupt and asset_id == 1 and pos == 5 and horizon == 1:
                        value = float(value) + 0.01
                    conn.execute(
                        "INSERT INTO market_daily_v003_labels VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (f"l{asset_id}_{pos}_{horizon}", state_id, asset_id, days[pos],
                         target_day, horizon, value, overlap, status,
                         "market_daily_reaction_v003_core"),
                    )


class TemporalDatasetV001Tests(unittest.TestCase):
    def make_fixture(self, root: Path, *, corrupt: bool = False):
        source, core = root / "market_data_v2.db", root / "core.db"
        output, reports = root / "temporal.db", root / "reports"
        config = root / "config.json"
        days = make_source(source)
        make_core(core, days, corrupt=corrupt)
        config.write_text(json.dumps(contract()), encoding="utf-8")
        return source, core, output, reports, config

    def test_tau_strategies_cover_integer_domain(self):
        cfg = contract()
        strategy, sparse = resolve_taus(cfg)
        self.assertEqual(strategy, "configured_sparse")
        self.assertEqual(len(sparse), 17)
        _, plus = resolve_taus(cfg, "configured_plus", [4, 251])
        self.assertIn(4, plus)
        self.assertIn(251, plus)
        _, dense = resolve_taus(cfg, "dense_all")
        self.assertEqual(dense, list(range(1, 253)))
        self.assertEqual(parse_tau_spec("4,11,22-25"), [4, 11, 22, 23, 24, 25])

    def test_materialization_passes_parity_is_read_only_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, core, output, reports, config = self.make_fixture(root)
            before = (sha(source), sha(core))
            first = materialize(source, core, output, config, reports)
            self.assertEqual(first["integrity_status"], "PASS")
            self.assertEqual(first["parity"]["status"], "PASS")
            self.assertFalse(first["training_authorized"])
            self.assertEqual(before, (sha(source), sha(core)))
            output_mtime = output.stat().st_mtime_ns
            second = materialize(source, core, output, config, reports)
            self.assertTrue(second["reused_existing"])
            self.assertEqual(output_mtime, output.stat().st_mtime_ns)
            self.assertEqual(before, (sha(source), sha(core)))
            with sqlite3.connect(output) as conn:
                self.assertEqual(conn.execute(
                    "SELECT COUNT(*) FROM temporal_outcomes"
                ).fetchone()[0], 10 * 17)
                self.assertGreater(conn.execute(
                    "SELECT COUNT(*) FROM selection_by_horizon "
                    "WHERE corporate_action_overlap_origins>0"
                ).fetchone()[0], 0)
            with self.assertRaisesRegex(RuntimeError, "temporal training blocked"):
                require_training_authorized(output)
            self.assertTrue((reports / "parity_report.json").is_file())
            self.assertTrue((reports / "selection_report.json").is_file())

    def test_parity_failure_blocks_publication_and_writes_evidence(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, core, output, reports, config = self.make_fixture(root, corrupt=True)
            before = (sha(source), sha(core))
            with self.assertRaises(BuildBlocked):
                materialize(source, core, output, config, reports)
            self.assertFalse(output.exists())
            parity = json.loads((reports / "parity_report.json").read_text())
            self.assertEqual(parity["status"], "FAIL")
            self.assertGreater(parity["mismatch_counts_by_field"]["return_pct"], 0)
            self.assertEqual(before, (sha(source), sha(core)))

    def test_audit_replays_exact_core_parity(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, core, output, reports, config = self.make_fixture(root)
            materialize(source, core, output, config, reports)
            result = audit_output(output, core, contract())
            self.assertEqual(result["integrity_status"], "PASS")
            self.assertEqual(result["parity"]["compared_rows"], 40)
            self.assertEqual(result["training_gate_status"],
                             "BLOCKED_PENDING_LONG_HORIZON_SELECTION_REVIEW")


if __name__ == "__main__":
    unittest.main()

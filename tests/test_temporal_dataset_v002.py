from __future__ import annotations

import hashlib
import json
import math
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests.test_temporal_dataset_v001 import contract as v001_contract
from tools.temporal_dataset_v001 import materialize as materialize_v001
from tools.temporal_dataset_v002 import (
    BuildBlocked,
    audit_output,
    build_plan,
    compound_total_return_pct,
    economic_total_return_factor,
    materialize,
    provider_adjustment_control_factor,
    require_training_authorized,
)


ROOT = Path(__file__).resolve().parents[1]
V002_CONFIG = ROOT / "config" / "temporal_dataset_v002.json"


def sha(path: Path) -> str:
    return hashlib.sha256(path.read_bytes()).hexdigest()


def make_source(path: Path, *, corrupt_adjusted: bool = False) -> list[str]:
    days = [f"2025-{1 + index // 28:02d}-{1 + index % 28:02d}" for index in range(270)]
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE assets(
          asset_id INTEGER PRIMARY KEY,ticker TEXT,sector TEXT,
          asset_type TEXT,active INTEGER
        );
        CREATE TABLE daily_price_asof_configs(
          asof_contract_version TEXT,mode TEXT,cutoff_column TEXT,
          selection_point_in_time_verified INTEGER,adjusted_close_role TEXT
        );
        CREATE TABLE price_observations(
          price_observation_id TEXT PRIMARY KEY,asset_id INTEGER,trading_day TEXT,
          bar_end_utc TEXT,interval TEXT,close REAL,observed_adjusted_close REAL,
          observation_sequence INTEGER,observed_at TEXT,causal_available_at TEXT
        );
        CREATE VIEW daily_price_quality_gated_observations_v002 AS
          SELECT * FROM price_observations;
        CREATE TABLE corporate_action_versions(
          corporate_action_version_id TEXT PRIMARY KEY,is_present INTEGER,
          raw_value REAL,currency TEXT,action_time_utc TEXT,
          normalized_action_json TEXT
        );
        CREATE TABLE corporate_action_observations(
          action_observation_id TEXT PRIMARY KEY,corporate_action_version_id TEXT,
          asset_id INTEGER,effective_trading_day TEXT,action_type TEXT,
          observation_sequence INTEGER,observed_at TEXT,available_at TEXT,
          availability_basis TEXT,observation_kind TEXT
        );
        """)
        conn.execute("INSERT INTO daily_price_asof_configs VALUES(?,?,?,?,?)", (
            "daily_price_asof_v1", "historical_session_close_assumption",
            "available_at", 0, "audit_only_not_identity",
        ))
        dividend_index, split_index = 30, 60
        for asset_id in (1, 2):
            conn.execute("INSERT INTO assets VALUES(?,?,?,?,?)", (
                asset_id, f"T{asset_id}", "Tech" if asset_id == 1 else "Bank",
                "equity", 1,
            ))
            closes = [100.0 + asset_id + index * 0.1 for index in range(len(days))]
            adjusted = [closes[0] * 0.8]
            for index in range(1, len(days)):
                cash = 1.0 if asset_id == 1 and index == dividend_index else 0.0
                control = provider_adjustment_control_factor(
                    closes[index - 1], closes[index], cash
                )
                adjusted.append(adjusted[-1] * control)
            if corrupt_adjusted:
                adjusted[dividend_index] *= 1.01
            for index, day in enumerate(days):
                conn.execute(
                    "INSERT INTO price_observations VALUES(?,?,?,?,?,?,?,?,?,?)",
                    (
                        f"p{asset_id}_{index}", asset_id, day,
                        day + "T21:00:00+00:00", "1d", closes[index], adjusted[index],
                        1, "2026-01-01T00:00:00+00:00",
                        day + "T21:00:00+00:00",
                    ),
                )
        actions = [
            ("dv", "ao_div", days[dividend_index], "dividend", 1.0),
            ("sp", "ao_split", days[split_index], "stock_split", 4.0),
        ]
        for version_id, observation_id, day, action_type, raw_value in actions:
            normalized = json.dumps({
                "action_type": action_type, "effective_trading_day": day,
                "raw_value": raw_value, "is_present": True,
            }, sort_keys=True)
            conn.execute(
                "INSERT INTO corporate_action_versions VALUES(?,?,?,?,?,?)",
                (
                    version_id, 1, raw_value, None, day + "T14:30:00+00:00",
                    normalized,
                ),
            )
            conn.execute(
                "INSERT INTO corporate_action_observations VALUES(?,?,?,?,?,?,?,?,?,?)",
                (
                    observation_id, version_id, 1, day, action_type, 1,
                    "2026-01-01T00:00:00+00:00",
                    "2026-01-01T00:00:00+00:00",
                    "retrieval_time_no_announcement", "initial_observation",
                ),
            )
    return days


def make_core(path: Path, days: list[str]) -> None:
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
    action_days = {days[30], days[60]}
    with sqlite3.connect(path) as conn:
        conn.executescript("""
        CREATE TABLE build_metadata(key TEXT PRIMARY KEY,value_json TEXT);
        CREATE TABLE market_daily_v003_states(
          state_id TEXT PRIMARY KEY,asset_id INTEGER,ticker TEXT,sector TEXT,
          trading_day TEXT,state_time TEXT,feature_version TEXT
        );
        CREATE TABLE market_daily_v003_labels(
          label_id TEXT PRIMARY KEY,state_id TEXT,asset_id INTEGER,
          origin_trading_day TEXT,target_trading_day TEXT,horizon_sessions INTEGER,
          return_pct REAL,corporate_action_overlap INTEGER,label_status TEXT,
          label_version TEXT
        );
        """)
        metadata = {
            "config": cfg, "source_db": "/tmp/market_data_v2.db",
            "states": len(state_positions) * 2, "state_assets": 2,
            "state_first_day": days[min(state_positions)], "state_last_day": days[-1],
            "labels": len(state_positions) * 8,
        }
        for key, value in metadata.items():
            conn.execute(
                "INSERT INTO build_metadata VALUES(?,?)", (key, json.dumps(value))
            )
        for asset_id in (1, 2):
            ticker = f"T{asset_id}"
            sector = "Tech" if asset_id == 1 else "Bank"
            for position in state_positions:
                state_id = f"s{asset_id}_{position}"
                conn.execute(
                    "INSERT INTO market_daily_v003_states VALUES(?,?,?,?,?,?,?)",
                    (
                        state_id, asset_id, ticker, sector, days[position],
                        days[position] + "T21:00:00+00:00",
                        "market_daily_state_v003_core",
                    ),
                )
                for horizon in (1, 3, 5, 10):
                    target_position = position + horizon
                    if target_position >= len(days):
                        target_day, value, overlap, status = None, None, 0, "insufficient_future"
                    else:
                        target_day = days[target_position]
                        origin_close = 100.0 + asset_id + position * 0.1
                        target_close = 100.0 + asset_id + target_position * 0.1
                        value = 100.0 * (target_close / origin_close - 1.0)
                        overlap = int(
                            asset_id == 1
                            and any(days[position] < day <= target_day for day in action_days)
                        )
                        status = "corporate_action_overlap" if overlap else "usable"
                    conn.execute(
                        "INSERT INTO market_daily_v003_labels VALUES(?,?,?,?,?,?,?,?,?,?)",
                        (
                            f"l{asset_id}_{position}_{horizon}", state_id, asset_id,
                            days[position], target_day, horizon, value, overlap, status,
                            "market_daily_reaction_v003_core",
                        ),
                    )


class TemporalDatasetV002Tests(unittest.TestCase):
    def fixture(self, root: Path, *, corrupt_adjusted: bool = False):
        source, core = root / "market_data_v2.db", root / "core.db"
        v001, v002 = root / "temporal_v001.db", root / "temporal_v002.db"
        v001_cfg = root / "v001.json"
        days = make_source(source, corrupt_adjusted=corrupt_adjusted)
        make_core(core, days)
        v001_cfg.write_text(json.dumps(v001_contract()), encoding="utf-8")
        materialize_v001(
            source, core, v001, v001_cfg, root / "reports_v001"
        )
        return source, core, v001, v002, root / "reports_v002"

    def test_economic_and_provider_math_are_distinct_and_explicit(self):
        economic = economic_total_return_factor(123.66, 22.19, 103.75)
        provider = provider_adjustment_control_factor(123.66, 22.19, 103.75)
        self.assertAlmostEqual(economic, 125.94 / 123.66, places=14)
        self.assertAlmostEqual(provider, 22.19 / 19.91, places=14)
        self.assertGreater(abs(economic - provider), 0.09)
        split_normalized = economic_total_return_factor(100.0, 102.0, 0.0)
        self.assertEqual(split_normalized, 1.02)
        compounded = compound_total_return_pct(math.log(1.01) + math.log(102.0 / 98.0))
        self.assertAlmostEqual(compounded, 100.0 * (1.01 * 102.0 / 98.0 - 1.0))

    def test_materialization_recovers_actions_preserves_parity_and_is_idempotent(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, core, v001, v002, reports = self.fixture(root)
            before = (sha(source), sha(core), sha(v001))
            plan = build_plan(source, core, v001, V002_CONFIG)
            self.assertEqual(plan["status"], "READY")
            self.assertFalse(plan["split_factor_is_applied_to_return"])
            first = materialize(
                source, core, v001, v002, V002_CONFIG, reports
            )
            self.assertEqual(first["integrity_status"], "PASS")
            self.assertEqual(first["v001_parity"]["status"], "PASS")
            self.assertEqual(first["no_action_identity"]["status"], "PASS")
            self.assertEqual(first["action_reconciliation"]["status"], "PASS")
            self.assertEqual(before, (sha(source), sha(core), sha(v001)))
            with sqlite3.connect(v002) as conn:
                conn.row_factory = sqlite3.Row
                self.assertEqual(
                    conn.execute("SELECT COUNT(*) FROM temporal_outcomes").fetchone()[0],
                    10 * 17,
                )
                dividend_window = conn.execute(
                    """SELECT * FROM market_temporal_v002_outcomes
                       WHERE state_id='s1_20' AND tau_sessions=13"""
                ).fetchone()
                self.assertEqual(dividend_window["raw_close_label_status"],
                                 "corporate_action_overlap")
                self.assertEqual(dividend_window["total_return_label_status"], "usable")
                self.assertEqual(dividend_window["action_overlap_class"], "cash")
                self.assertNotAlmostEqual(
                    dividend_window["raw_close_return_pct"],
                    dividend_window["total_return_pct"], places=10,
                )
                split_window = conn.execute(
                    """SELECT * FROM market_temporal_v002_outcomes
                       WHERE state_id='s1_50' AND tau_sessions=13"""
                ).fetchone()
                self.assertEqual(split_window["action_overlap_class"], "split")
                self.assertAlmostEqual(
                    split_window["raw_close_return_pct"],
                    split_window["total_return_pct"], places=10,
                )
                self.assertEqual(
                    conn.execute(
                        "SELECT split_factor_product FROM temporal_return_steps "
                        "WHERE asset_id=1 AND trading_day=?", ("2025-03-05",)
                    ).fetchone()[0], 4.0,
                )
            for name in (
                "v001_parity_report.json", "no_action_identity_report.json",
                "action_reconciliation_report.json", "coverage_report.json", "audit.json",
            ):
                self.assertTrue((reports / name).is_file())
            with self.assertRaisesRegex(RuntimeError, "V002 training blocked"):
                require_training_authorized(v002)
            mtime = v002.stat().st_mtime_ns
            second = materialize(source, core, v001, v002, V002_CONFIG, reports)
            self.assertTrue(second["reused_existing"])
            self.assertEqual(mtime, v002.stat().st_mtime_ns)
            replay = audit_output(v002, v001, json.loads(V002_CONFIG.read_text()))
            self.assertEqual(replay["integrity_status"], "PASS")

    def test_provider_reconciliation_failure_blocks_publication(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, core, v001, v002, reports = self.fixture(
                root, corrupt_adjusted=True
            )
            before = (sha(source), sha(core), sha(v001))
            with self.assertRaises(BuildBlocked):
                materialize(source, core, v001, v002, V002_CONFIG, reports)
            self.assertFalse(v002.exists())
            action = json.loads(
                (reports / "action_reconciliation_report.json").read_text()
            )
            self.assertEqual(action["status"], "FAIL")
            self.assertGreater(action["provider_reconciliation_failures"], 0)
            self.assertEqual(before, (sha(source), sha(core), sha(v001)))

    def test_v001_parity_tamper_blocks_publication(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            source, core, v001, v002, reports = self.fixture(root)
            with sqlite3.connect(v001) as conn:
                conn.execute(
                    "UPDATE temporal_outcomes SET return_pct=return_pct+0.01 "
                    "WHERE origin_id=(SELECT MIN(origin_id) FROM temporal_outcomes) "
                    "AND tau_sessions=1"
                )
            before = (sha(source), sha(core), sha(v001))
            with self.assertRaises(BuildBlocked):
                materialize(source, core, v001, v002, V002_CONFIG, reports)
            self.assertFalse(v002.exists())
            parity = json.loads((reports / "v001_parity_report.json").read_text())
            self.assertEqual(parity["status"], "FAIL")
            self.assertGreater(parity["mismatch_counts_by_field"][
                "raw_close_return_pct"
            ], 0)
            self.assertEqual(before, (sha(source), sha(core), sha(v001)))


if __name__ == "__main__":
    unittest.main()

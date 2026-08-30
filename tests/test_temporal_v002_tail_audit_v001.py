from __future__ import annotations

import sqlite3
import unittest

from tools.temporal_v002_tail_audit_v001 import _audit_one


class TemporalV002TailAuditV001Tests(unittest.TestCase):
    def test_zero_log_factor_is_valid_identity_not_missing(self) -> None:
        conn = sqlite3.connect(":memory:")
        conn.row_factory = sqlite3.Row
        conn.executescript(
            "CREATE TABLE temporal_origins(origin_id INTEGER,state_id TEXT,asset_id INTEGER,ticker TEXT,"
            "origin_trading_day TEXT,origin_session_index INTEGER,provider_close_origin REAL);"
            "CREATE TABLE temporal_outcomes(origin_id INTEGER,tau_sessions INTEGER,target_trading_day TEXT,"
            "raw_close_return_pct REAL,total_return_pct REAL,cash_distribution_count INTEGER,"
            "split_action_count INTEGER,action_overlap_class TEXT,total_return_label_status TEXT);"
            "CREATE TABLE temporal_price_points(asset_id INTEGER,asset_session_index INTEGER,provider_close REAL);"
            "CREATE TABLE temporal_return_steps(asset_id INTEGER,asset_session_index INTEGER,trading_day TEXT,"
            "provider_close_previous REAL,provider_close_current REAL,cash_distribution REAL,"
            "split_factor_product REAL,cash_action_count INTEGER,split_action_count INTEGER,"
            "economic_gross_factor REAL,log_economic_gross_factor REAL,step_status TEXT);"
            "INSERT INTO temporal_origins VALUES(1,'state-a',1,'AAA','2020-01-01',10,100.0);"
            "INSERT INTO temporal_outcomes VALUES(1,1,'2020-01-02',0.0,0.0,0,0,'none','usable');"
            "INSERT INTO temporal_price_points VALUES(1,11,100.0);"
            "INSERT INTO temporal_return_steps VALUES(1,11,'2020-01-02',100.0,100.0,0.0,1.0,0,0,1.0,0.0,'usable_no_action');"
        )
        result = _audit_one(
            conn, {"state_id": "state-a", "tau_sessions": 1},
            return_tolerance=1e-10, log_tolerance=1e-12, move_threshold=20.0,
        )
        conn.close()
        self.assertEqual(result["failures"], [])
        self.assertEqual(result["economic_product_absolute_error_pct"], 0.0)
        self.assertEqual(result["prefix_absolute_error_pct"], 0.0)


if __name__ == "__main__":
    unittest.main()

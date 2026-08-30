from __future__ import annotations

import json
from pathlib import Path
import sqlite3
import tempfile
import unittest

from tools.temporal_v002_selection_mask_v001 import audit_mask, build_mask, digest


class TemporalV002SelectionMaskV001Tests(unittest.TestCase):
    def test_quarantine_excludes_only_paths_containing_exact_step(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            database = root / "v002.sqlite"
            conn = sqlite3.connect(database)
            conn.executescript(
                "CREATE TABLE temporal_return_steps(asset_id INTEGER,asset_session_index INTEGER,trading_day TEXT);"
                "CREATE TABLE temporal_origins(origin_id INTEGER,state_id TEXT,asset_id INTEGER,origin_session_index INTEGER);"
                "CREATE TABLE temporal_outcomes(origin_id INTEGER,tau_sessions INTEGER,total_return_label_status TEXT);"
                "INSERT INTO temporal_return_steps VALUES(1,10,'2020-01-10');"
                "INSERT INTO temporal_origins VALUES(1,'s1',1,8),(2,'s2',1,10),(3,'s3',2,8);"
                "INSERT INTO temporal_outcomes VALUES(1,1,'usable'),(1,2,'usable'),(1,5,'usable'),"
                "(2,5,'usable'),(3,5,'usable');"
            )
            conn.commit()
            conn.close()
            review = root / "review.json"
            review.write_text(json.dumps({
                "flagged_events": [{
                    "review_id": "event-a", "asset_id": 1, "trading_day": "2020-01-10",
                    "decision_required": True, "disposition": "quarantine",
                }], "quarantined_review_ids": ["event-a"],
            }))
            config = root / "config.json"
            config.write_text(json.dumps({
                "version": "market_temporal_v002_selection_mask_v001",
                "expected_v002_sha256": digest(database),
                "source_database_mutation_allowed": False,
            }))
            output = root / "out"
            build = build_mask(database, review, config, output)
            self.assertEqual(build["excluded_unique_pairs"], 2)
            mask = sqlite3.connect(output / "selection_mask.sqlite")
            rows = mask.execute("SELECT origin_id,tau_sessions FROM excluded_outcomes ORDER BY 1,2").fetchall()
            mask.close()
            self.assertEqual(rows, [(1, 2), (1, 5)])
            audit = audit_mask(database, review, config, output)
            self.assertEqual(audit["status"], "PASS")
            self.assertTrue(audit["source_opened_read_only"])


if __name__ == "__main__":
    unittest.main()

from __future__ import annotations

import json
import sqlite3
import tempfile
import unittest
from pathlib import Path

from tests import test_temporal_dataset_v002 as v002_test_support
from tools.temporal_dataset_v002 import materialize
from tools.temporal_v002_review import (
    derive_total_return_from_prefix,
    deterministic_sampled_tau,
    run_review,
)
from tools.temporal_distributional_preregistration_v001 import build_plan


ROOT = Path(__file__).resolve().parents[1]
V002_CONFIG = ROOT / "config" / "temporal_dataset_v002.json"
REVIEW_CONFIG = ROOT / "config" / "temporal_v002_review.json"
MODEL_CONFIG = ROOT / "config" / "temporal_distributional_preregistration_v001.json"


def adjusted_review_config(path: Path) -> None:
    payload = json.loads(REVIEW_CONFIG.read_text(encoding="utf-8"))
    payload["special_cash_review"]["moderate_cash_to_previous_close"] = 0.005
    payload["special_cash_review"]["critical_cash_to_previous_close"] = 0.008
    payload["distribution_audit"]["quantile_sample_origin_modulus"] = 1
    payload["on_demand_tau_audit"]["origin_sample_modulus"] = 1
    payload["on_demand_tau_audit"]["maximum_sampled_origins"] = 20
    path.write_text(json.dumps(payload), encoding="utf-8")


def adjusted_model_config(path: Path) -> None:
    payload = json.loads(MODEL_CONFIG.read_text(encoding="utf-8"))
    payload["model_contract"]["frozen_own_features"] = []
    payload["horizon_contract"]["development_training_anchors"] = [
        1, 2, 3, 5, 8, 10, 13, 21, 34, 63, 126
    ]
    payload["horizon_contract"]["sealed_generalization_holdouts"] = [
        7, 17, 42, 90, 180, 252
    ]
    payload["outer_evaluation"]["folds"] = 2
    payload["outer_evaluation"]["initial_fraction"] = 0.4
    payload["outer_evaluation"]["minimum_common_support_origin_days"] = 2
    payload["outer_evaluation"]["minimum_train_rows_per_anchor"] = 0
    payload["outer_evaluation"]["minimum_test_rows_per_anchor"] = 0
    path.write_text(json.dumps(payload), encoding="utf-8")


class TemporalV002ReviewTests(unittest.TestCase):
    def fixture(self, root: Path):
        source, core, v001, v002, reports = (
            v002_test_support.TemporalDatasetV002Tests().fixture(root)
        )
        materialize(source, core, v001, v002, V002_CONFIG, reports)
        review_cfg = root / "review.json"
        model_cfg = root / "model.json"
        adjusted_review_config(review_cfg)
        adjusted_model_config(model_cfg)
        return core, v002, review_cfg, model_cfg

    def test_prefix_target_and_tau_sampler_are_deterministic(self):
        prefix = [0.0, 0.01, 0.03, 0.025]
        expected = 100.0 * (__import__("math").exp(0.025) - 1.0)
        self.assertAlmostEqual(derive_total_return_from_prefix(prefix, 0, 3), expected)
        self.assertIsNone(derive_total_return_from_prefix(prefix, 2, 2))
        first = deterministic_sampled_tau("state", 1, 10, 7, {1, 3, 5, 7})
        second = deterministic_sampled_tau("state", 1, 10, 7, {1, 3, 5, 7})
        self.assertEqual(first, second)
        self.assertNotIn(first, {1, 3, 5, 7})

    def test_review_requires_bound_special_action_decisions_then_passes(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            core, v002, review_cfg, model_cfg = self.fixture(root)
            reports = root / "review_reports"
            pending = run_review(v002, review_cfg, reports)
            self.assertEqual(pending["status"], "REVIEW_REQUIRED_SPECIAL_ACTIONS")
            action = json.loads((reports / "economic_action_review.json").read_text())
            self.assertEqual(action["flagged_steps"], 1)
            template_path = reports / "special_action_decisions_template.json"
            decisions = json.loads(template_path.read_text())
            decisions["decisions"][0].update({
                "disposition": "validated_cash_and_share_entitlement",
                "evidence": ["synthetic_fixture_explicit_cash_distribution"],
                "rationale": "Synthetic fixture explicitly preserves one share plus cash.",
            })
            decision_path = reports / "special_action_decisions.json"
            decision_path.write_text(json.dumps(decisions), encoding="utf-8")
            passed = run_review(v002, review_cfg, reports, decision_path)
            self.assertEqual(passed["status"], "PASS")
            model_reports = root / "model_reports"
            plan = build_plan(
                v002, core, reports / "audit.json", model_cfg, model_reports
            )
            self.assertEqual(plan["status"], "READY_FOR_RUNNER_IMPLEMENTATION_NO_TRAINING")
            self.assertFalse(plan["training_authorized"])
            self.assertFalse(plan["holdout_values_or_performance_read"])
            self.assertTrue((model_reports / "fold_plan.json").is_file())

    def test_negative_cash_uplift_tamper_fails_review(self):
        with tempfile.TemporaryDirectory() as td:
            root = Path(td)
            _, v002, review_cfg, _ = self.fixture(root)
            with sqlite3.connect(v002) as conn:
                conn.execute(
                    "UPDATE temporal_outcomes SET total_return_pct=raw_close_return_pct-1 "
                    "WHERE origin_id=(SELECT MIN(origin_id) FROM temporal_outcomes) "
                    "AND tau_sessions=1"
                )
            result = run_review(v002, review_cfg, root / "tamper_reports")
            self.assertEqual(result["status"], "FAIL")
            target = json.loads(
                (root / "tamper_reports" / "target_distribution_report.json").read_text()
            )
            self.assertEqual(target["status"], "FAIL")
            self.assertTrue(any("NEGATIVE_CASH_UPLIFT" in value for value in target["failures"]))


if __name__ == "__main__":
    unittest.main()

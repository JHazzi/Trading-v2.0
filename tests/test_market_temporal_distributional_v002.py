from __future__ import annotations

import json
from pathlib import Path
import tempfile
import unittest

import numpy as np

from evaluation.market.temporal_distributional_v002 import development_gate, holdout_gate
from models.market.temporal_distributional_v002 import (
    combine_log_quantiles,
    fit_to_target,
    horizon_shrinkage,
    residual_targets,
    target_to_fit,
)
from pipeline.market_temporal_distributional_v002 import (
    Panel,
    _causal_oof_base,
    _panel_weights,
    _verify_freeze,
    load_config,
)
from tools.temporal_distributional_preregistration_v002 import load_contract


ROOT = Path(__file__).resolve().parents[1]


class TemporalDistributionalV002Tests(unittest.TestCase):
    def test_exact_log_wealth_residual_identity_and_shrinkage(self) -> None:
        actual_pct = np.asarray([-50.0, 0.0, 100.0])
        actual_log = target_to_fit(actual_pct)
        base = np.column_stack((actual_log - 0.2, actual_log, actual_log + 0.2))
        residual = residual_targets(actual_log, base)
        np.testing.assert_allclose(residual, np.asarray([[0.2, 0.0, -0.2]] * 3))
        np.testing.assert_allclose(horizon_shrinkage([1, 64]), [1.0, 0.5])
        combined = combine_log_quantiles(base, residual, [1, 1, 1])
        np.testing.assert_allclose(fit_to_target(combined), np.repeat(actual_pct[:, None], 3, axis=1))

    def test_canonical_runner_contract_is_hash_bound(self) -> None:
        cfg = load_config(ROOT / "config" / "temporal_distributional_runner_v002.json")
        self.assertEqual(cfg["models"]["model_version"], "market_temporal_distributional_residual_v002")
        self.assertEqual(cfg["development_gate"]["no_harm_horizons"], [126, 252])

    def test_preregistration_rejects_noncausal_crossfit(self) -> None:
        source = ROOT / "config" / "temporal_distributional_preregistration_v002.json"
        payload = json.loads(source.read_text(encoding="utf-8"))
        payload["internal_cross_fitting"]["split_policy"] = "random_kfold"
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / "bad.json"
            path.write_text(json.dumps(payload), encoding="utf-8")
            with self.assertRaisesRegex(ValueError, "invalid_internal_crossfit_policy"):
                load_contract(path)

    def test_panel_weights_equalize_days_and_origins(self) -> None:
        panel = Panel(
            origin_id=np.asarray([1, 1, 2, 3, 3, 3]),
            origin_day_index=np.asarray([0, 0, 0, 1, 1, 1]),
            origin_days=["2020-01-01", "2020-01-02"],
            tau_sessions=np.asarray([1, 2, 1, 1, 2, 3]),
            target_trading_day=np.asarray(["2020-01-02"] * 6),
            actual_pct=np.zeros(6),
            own_state=np.zeros((6, 2), dtype="float32"),
        )
        weights = _panel_weights(panel)
        self.assertAlmostEqual(float(weights[:3].sum()), float(weights[3:].sum()))
        self.assertAlmostEqual(float(weights[:2].sum()), float(weights[2]))

    def test_internal_crossfit_is_forward_only_and_target_day_purged(self) -> None:
        rng = np.random.default_rng(7)
        days = np.asarray([f"2020-01-{day:02d}" for day in range(1, 31)])
        origin_day_index = np.repeat(np.arange(len(days)), 12)
        origin_id = np.repeat(np.arange(len(days) * 4), 3)
        tau = np.tile([1, 5, 21], len(days) * 4)
        vol = rng.uniform(0.5, 3.0, len(origin_id)).astype("float32")
        state = np.column_stack((vol, rng.normal(size=len(origin_id)))).astype("float32")
        actual = rng.normal(0.0, vol / 10.0).astype(float)
        panel = Panel(
            origin_id, origin_day_index.astype("int16"), days.tolist(), tau,
            days[origin_day_index], actual, state,
        )
        profile = {
            "learning_rate": 0.1, "max_iter": 3, "max_leaf_nodes": 5,
            "min_samples_leaf": 5, "l2_regularization": 1.0,
            "early_stopping": False, "random_seed": 42,
        }
        cfg = {
            "quantiles": [0.25, 0.5, 0.75],
            "models": {
                "own_features": ["asset_vol_63d_pct", "other"],
                "scale_feature": "asset_vol_63d_pct", "profile_base": profile,
            },
            "internal_cross_fitting": {
                "folds": 5, "initial_fraction_of_outer_training_origin_days": 0.35,
                "minimum_oof_rows": 100,
            },
        }
        usable, predictions, audit = _causal_oof_base(panel, cfg)
        self.assertGreater(int(usable.sum()), 100)
        self.assertTrue(np.isfinite(predictions[usable]).all())
        self.assertTrue(all(row["latest_training_target_day"] < row["first_validation_origin_day"] for row in audit))
        self.assertTrue(all(row["future_rows_used"] is False for row in audit))

    def test_development_and_holdout_no_harm_are_hard_gates(self) -> None:
        summary = {
            "candidate_vs_reference": {"point_delta_pct": 0.1, "bootstrap": {"252": {"ci95": [0.01, 0.2]}}},
            "per_anchor_point_delta_pct": {"126": 0.01, "252": -0.001},
            "positive_anchors": 10, "positive_folds": 5, "improved_quantiles": 5,
            "candidate_calibration_mae": 0.01, "reference_calibration_mae": 0.02,
            "candidate_vs_mean_placebo": {"bootstrap": {"252": {"ci95": [0.01, 0.2]}}},
            "candidate_vs_each_placebo_point": {"11": 0.1, "29": 0.1},
        }
        rules = {
            "minimum_positive_development_anchors": 8, "minimum_positive_outer_folds": 4,
            "minimum_improved_quantiles": 3, "no_harm_horizons": [126, 252],
            "no_harm_margin_pct": 0.0,
        }
        self.assertEqual(development_gate(summary, rules)["status"], "INCONCLUSIVE_OR_AUXILIARY_GATE_FAIL_NO_HOLDOUT_OPEN")
        holdout_summary = {
            **summary, "per_holdout_horizon_point_delta_pct": {"180": -0.001},
            "positive_holdout_horizons": 5,
        }
        holdout_rules = {
            "minimum_positive_holdout_taus": 4, "minimum_positive_outer_folds": 4,
            "minimum_improved_quantiles": 3, "no_harm_holdout_horizons": [180],
            "no_harm_margin_pct": 0.0,
        }
        self.assertTrue(holdout_gate(holdout_summary, holdout_rules)["status"].startswith("FAIL_"))

    def test_negative_closure_blocks_any_freeze_use(self) -> None:
        with tempfile.TemporaryDirectory() as directory:
            output = Path(directory)
            (output / "development_closure.json").write_text("{}", encoding="utf-8")
            with self.assertRaisesRegex(RuntimeError, "negative_branch_closure_blocks_holdout"):
                _verify_freeze(output)


if __name__ == "__main__":
    unittest.main()

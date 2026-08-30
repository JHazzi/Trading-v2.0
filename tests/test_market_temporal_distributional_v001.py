from __future__ import annotations

import unittest

import numpy as np

from evaluation.market.temporal_distributional_v001 import (
    development_gate,
    equal_tau_day_calibration,
    equal_tau_day_table,
    holdout_gate,
    moving_block_bootstrap,
)
from models.market.temporal_distributional_v001 import (
    fit_to_target,
    monotone_rearrange,
    target_to_fit,
    tau_coordinates,
)
from pipeline.market_temporal_distributional_v001 import (
    Panel,
    _anchor_selected,
    console_payload,
    _fit_models,
    _placebo_design,
    _predict_models,
    _predict_simple,
)


class TemporalDistributionalV001Tests(unittest.TestCase):
    def test_log_total_wealth_transform_is_exact_and_unclipped(self) -> None:
        values = np.asarray([-99.5, -50.0, 0.0, 25.0, 1707.95])
        np.testing.assert_allclose(fit_to_target(target_to_fit(values)), values, rtol=1e-12, atol=1e-12)
        with self.assertRaises(ValueError):
            target_to_fit(np.asarray([-100.0]))

    def test_tau_domain_and_coordinates(self) -> None:
        coordinates = tau_coordinates([1, 7, 252])
        self.assertEqual(coordinates.shape, (3, 3))
        self.assertAlmostEqual(float(coordinates[-1, 1]), 1.0)
        with self.assertRaises(ValueError):
            tau_coordinates([0])
        with self.assertRaises(ValueError):
            tau_coordinates([253])
        with self.assertRaises(ValueError):
            tau_coordinates([1.5])

    def test_monotone_rearrangement(self) -> None:
        result = monotone_rearrange(np.asarray([[3.0, 1.0, 2.0], [-1.0, 5.0, 0.0]]))
        self.assertTrue(np.all(np.diff(result, axis=1) >= 0.0))

    def test_training_anchor_hash_is_outcome_free_and_balanced(self) -> None:
        anchors = [1, 2, 3, 5, 8, 10, 13, 21, 34, 63, 126, 252]
        counts = {tau: 0 for tau in anchors}
        for state in range(20_000):
            selected = [tau for tau in anchors if _anchor_selected(f"state-{state}", tau, anchors, [0, 4, 8])]
            self.assertEqual(len(selected), 3)
            for tau in selected:
                counts[tau] += 1
        self.assertLess(max(counts.values()) / min(counts.values()), 1.06)

    def test_simple_reference_interpolates_unseen_tau_without_labels(self) -> None:
        simple = {"1": [0.0, 0.1], "9": [0.8, 0.9]}
        prediction = _predict_simple(simple, np.asarray([1, 4, 9]))
        self.assertEqual(prediction.shape, (3, 2))
        np.testing.assert_allclose(prediction[0], fit_to_target([0.0, 0.1]), rtol=1e-6)
        np.testing.assert_allclose(prediction[2], fit_to_target([0.8, 0.9]), rtol=1e-6)
        self.assertTrue(np.all(prediction[1] > prediction[0]))
        self.assertTrue(np.all(prediction[1] < prediction[2]))

    def test_equal_tau_then_day_weighting(self) -> None:
        # H1 has 100 rows and delta 1; H2 has one row and delta 3.  Equal-tau
        # weighting must return 2, not the row-weighted value near 1.
        days = np.zeros(101, dtype=int)
        tau = np.asarray([1] * 100 + [2])
        baseline = np.asarray([1.0] * 100 + [3.0])
        candidate = np.zeros(101)
        daily = equal_tau_day_table(days, tau, baseline, candidate, ["2020-01-02"])
        self.assertAlmostEqual(float(daily.iloc[0]["loss_delta_baseline_minus_candidate"]), 2.0)

    def test_calibration_also_weights_tau_then_day(self) -> None:
        days = np.zeros(101, dtype=int)
        tau = np.asarray([1] * 100 + [2])
        actual = np.asarray([0.0] * 100 + [10.0])
        prediction = np.zeros((101, 1))
        records = equal_tau_day_calibration(days, tau, actual, prediction, [0.5], ["2020-01-02"])
        # H1 CDF=1 and H2 CDF=0, hence equal-tau calibration CDF=0.5.
        self.assertAlmostEqual(records["0.5"][0]["empirical_cdf"], 0.5)

    def test_placebo_uses_one_donor_per_origin_across_tau(self) -> None:
        own_features = [
            "asset_drawdown_20d_pct", "asset_drawdown_252d_pct", "asset_drawdown_63d_pct",
            "asset_range_1d_pct", "asset_return_10d_pct", "asset_return_1d_pct",
            "asset_return_20d_pct", "asset_return_3d_pct", "asset_return_5d_pct",
            "asset_return_63d_pct", "asset_vol_20d_pct", "asset_vol_5d_pct",
            "asset_vol_63d_pct", "asset_volume_ratio_20d",
        ]
        cfg = {"models": {"own_features": own_features, "preserved_placebo_features": [
            "asset_vol_5d_pct", "asset_vol_20d_pct", "asset_vol_63d_pct"
        ]}}
        origins = np.repeat(np.arange(4), 3)
        tau = np.tile([1, 5, 21], 4)
        day = np.repeat([0, 0, 1, 1], 3)
        base = np.repeat(np.arange(4, dtype=float).reshape(-1, 1), 14, axis=1)
        own = np.repeat(base, 3, axis=0).astype("float32")
        panel = Panel(origins, day, ["d0", "d1"], tau, np.zeros(12), own)
        design = np.column_stack((own, tau_coordinates(tau))).astype("float32")
        placebo, audit = _placebo_design(panel, design, cfg, 11)
        self.assertTrue(audit["tau_coordinates_preserved"])
        np.testing.assert_array_equal(placebo[:, -3:], design[:, -3:])
        for origin in range(4):
            rows = origins == origin
            self.assertEqual(len(set(placebo[rows, 0].tolist())), 1)
            self.assertNotEqual(float(placebo[rows, 0][0]), float(origin))
        for feature in cfg["models"]["preserved_placebo_features"]:
            index = own_features.index(feature)
            np.testing.assert_array_equal(placebo[:, index], design[:, index])

    def test_bootstrap_and_gates_are_deterministic(self) -> None:
        values = np.linspace(0.1, 1.0, 300)
        first = moving_block_bootstrap(values, 21, 200, 42)
        second = moving_block_bootstrap(values, 21, 200, 42)
        self.assertEqual(first, second)
        summary = {
            "candidate_vs_reference": {"point_delta_pct": 1.0, "bootstrap": {"252": {"ci95": [0.1, 1.9]}}},
            "positive_anchors": 12, "positive_folds": 5, "improved_quantiles": 5,
            "candidate_calibration_mae": 0.01, "reference_calibration_mae": 0.02,
            "candidate_vs_mean_placebo": {"bootstrap": {"252": {"ci95": [0.05, 1.0]}}},
            "candidate_vs_each_placebo_point": {"11": 0.1, "29": 0.2},
        }
        rules = {"minimum_positive_anchors": 8, "minimum_positive_folds": 4, "minimum_improved_quantiles": 3}
        self.assertTrue(development_gate(summary, rules)["status"].startswith("PASS_"))
        holdout_summary = dict(summary)
        holdout_summary["positive_holdout_horizons"] = 5
        holdout_rules = {"minimum_positive_holdout_horizons": 3, "minimum_positive_folds": 4, "minimum_improved_quantiles": 3}
        self.assertTrue(holdout_gate(holdout_summary, holdout_rules)["status"].startswith("PASS_"))

    def test_real_hgb_quantile_api_fits_and_backtransforms(self) -> None:
        rng = np.random.default_rng(123)
        design = rng.normal(size=(300, 5)).astype("float32")
        total_return = 100.0 * np.expm1(0.05 * design[:, 0] + rng.normal(0.0, 0.02, 300))
        cfg = {
            "quantiles": [0.25, 0.5, 0.75],
            "models": {"profile": {
                "learning_rate": 0.1, "max_iter": 4, "max_leaf_nodes": 7,
                "min_samples_leaf": 10, "l2_regularization": 1.0,
                "early_stopping": False, "random_seed": 42,
            }},
        }
        models = _fit_models(design, target_to_fit(total_return), cfg)
        prediction = _predict_models(models, design[:20], cfg["quantiles"])
        self.assertEqual(prediction.shape, (20, 3))
        self.assertTrue(np.isfinite(prediction).all())
        self.assertTrue(np.all(np.diff(prediction, axis=1) >= 0.0))

    def test_fold_console_is_compact_but_report_remains_persisted(self) -> None:
        full = {
            "version": "v", "phase": "development", "fold": 1, "status": "PASS", "rows": 10,
            "daily": {"large": [1, 2, 3]}, "diagnostics": {"train_rows": 20},
            "point_delta_reference_minus_candidate_pct": 0.1, "artifacts": {},
        }
        shown = console_payload("develop-fold", full)
        self.assertNotIn("daily", shown)
        self.assertTrue(shown["complete_report_persisted"])
        self.assertEqual(shown["train_rows"], 20)


if __name__ == "__main__":
    unittest.main()

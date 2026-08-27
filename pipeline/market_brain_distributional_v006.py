from __future__ import annotations

import argparse
import json
from pathlib import Path

from evaluation.market.distributional_v006_audit import audit
from models.market.distributional_v006_baselines import (
    DEFAULT_CONFIG,
    DEFAULT_CORE_DB,
    load_config,
    run_horizon,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = (
    ROOT
    / "reports"
    / "market_brain_distributional_v006"
    / "empirical_baseline_v001"
)


def _json_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def plan(core_db: Path, config_path: Path) -> dict[str, object]:
    cfg = load_config(config_path)
    foundation = audit(core_db, config_path)
    return {
        "status": foundation["status"],
        "failures": foundation["failures"],
        "benchmark_version": cfg["version"],
        "model_version": cfg["model_version"],
        "frozen_hypothesis": (
            "A train-only empirical return shape, rescaled at prediction "
            "time by causal 20-session asset volatility, improves "
            "origin-day-equal-weight pinball loss relative to the "
            "unconditional train empirical distribution."
        ),
        "mathematical_definitions": {
            "baseline": "Q_train(q)",
            "candidate": (
                "median_train + Q_train((R-median_train)/vol20)(q) "
                "* vol20_t"
            ),
            "nonpositive_scale": "fall back to Q_train(q)",
            "probability_positive": (
                "empirical survival probability of the standardized "
                "training distribution at (0-median_train)/vol20_t"
            ),
        },
        "primary_comparison": {
            "baseline": cfg["primary_baseline"],
            "candidate": cfg["primary_candidate"],
            "score": cfg["primary_score"],
            "positive_delta_means_candidate_better": True,
        },
        "secondary_reference": cfg["secondary_reference"],
        "quantiles": cfg["quantiles"],
        "horizons_sessions": cfg["horizons_sessions"],
        "folds": {
            "outer_folds": cfg["outer_folds"],
            "initial_fraction": cfg["initial_fraction"],
            "policy": "same purged expanding origin-day design as V003",
        },
        "resampling": {
            "unit": "origin_trading_day",
            "moving_block_lengths": cfg[
                "moving_block_lengths_origin_days"
            ],
            "reps": cfg["bootstrap_reps"],
            "seed": cfg["bootstrap_seed"],
        },
        "claim_boundaries": {
            "no_best_horizon_selection": True,
            "no_model_or_hyperparameter_selection": True,
            "terminal_return_only": True,
            "not_a_trajectory_model": True,
            "not_strict_historical_pit": True,
            "not_production_ready": True,
        },
        "foundation_audit": foundation,
    }


def benchmark(
    core_db: Path,
    config_path: Path,
    output_dir: Path,
) -> dict[str, object]:
    cfg = load_config(config_path)
    foundation = audit(core_db, config_path)
    if foundation["status"] != "PASS":
        raise RuntimeError(
            f"distributional foundation audit failed: {foundation['failures']}"
        )

    horizon_reports = {}
    for horizon in cfg["horizons_sessions"]:
        report, daily = run_horizon(core_db, int(horizon), cfg)
        _json_write(
            output_dir / f"h{int(horizon)}_benchmark.json",
            report,
        )
        daily.to_csv(
            output_dir / f"h{int(horizon)}_primary_daily_losses.csv",
            index=False,
        )
        horizon_reports[str(horizon)] = {
            "oos_rows": report["oos_rows"],
            "oos_origin_days": report["oos_origin_days"],
            "primary_comparison": report["primary_comparison"],
            "primary_moving_block_bootstrap": report[
                "primary_moving_block_bootstrap"
            ],
            "pooled_metrics": report["pooled_metrics"],
        }

    summary = {
        "status": "PASS",
        "benchmark_version": cfg["version"],
        "model_version": cfg["model_version"],
        "dataset_contract": cfg["dataset_contract"],
        "market_feature_version": cfg["market_feature_version"],
        "label_version": cfg["label_version"],
        "target": cfg["target"],
        "horizons": horizon_reports,
        "interpretation_contract": {
            "primary_is_volatility_scaled_vs_train_empirical": True,
            "all_horizons_must_be_reported": True,
            "asset_empirical_is_secondary_only": True,
            "positive_delta_means_lower_pinball_loss": True,
            "no_learned_model_tested": True,
            "no_production_claim": True,
        },
    }
    _json_write(output_dir / "benchmark_summary.json", summary)
    return summary


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage", required=True, choices=("plan", "audit", "benchmark")
    )
    parser.add_argument("--core-db", type=Path, default=DEFAULT_CORE_DB)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument(
        "--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR
    )
    args = parser.parse_args()

    if args.stage == "plan":
        result = plan(args.core_db, args.config)
        _json_write(args.output_dir / "benchmark_plan.json", result)
    elif args.stage == "audit":
        result = audit(args.core_db, args.config)
        _json_write(args.output_dir / "foundation_audit.json", result)
    else:
        result = benchmark(args.core_db, args.config, args.output_dir)

    print(json.dumps(result, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()

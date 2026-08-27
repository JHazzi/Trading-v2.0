from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.market.distributional_v0061_robustness import (
    DEFAULT_CONFIG,
    DEFAULT_CORE_DB,
    DEFAULT_SOURCE_REPORT_DIR,
    load_config,
    run_horizon_robustness,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = (
    ROOT
    / "reports"
    / "market_brain_distributional_v0061"
    / "robustness_v001"
)


def _json_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _parse_horizons(text: str, allowed: list[int]) -> list[int]:
    requested = [int(x.strip()) for x in text.split(",") if x.strip()]
    if not requested:
        raise ValueError("at least one horizon required")
    if len(set(requested)) != len(requested):
        raise ValueError("duplicate horizons")
    invalid = sorted(set(requested) - set(allowed))
    if invalid:
        raise ValueError(f"unsupported horizons: {invalid}")
    return requested


def plan(
    config_path: Path,
    core_db: Path,
    source_report_dir: Path,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    required_source = []
    missing = []
    for horizon in cfg["horizons_sessions"]:
        for suffix in ("benchmark.json", "primary_daily_losses.csv"):
            path = source_report_dir / f"h{int(horizon)}_{suffix}"
            required_source.append(str(path))
            if not path.exists():
                missing.append(str(path))
    if not core_db.exists():
        missing.append(str(core_db))
    return {
        "status": "PASS" if not missing else "FAIL",
        "robustness_version": cfg["version"],
        "source_benchmark_version": cfg["source_benchmark_version"],
        "source_model_version": cfg["source_model_version"],
        "frozen_question": (
            "Does the completed V006 conditional-dispersion result survive exact "
            "reproduction plus predeclared diagnostics for temporal folds, tails, "
            "asset/sector concentration, train-defined volatility regimes, calibration "
            "drift and alternative causal volatility scales?"
        ),
        "unchanged_primary": {
            "baseline": cfg["primary_baseline"],
            "candidate": cfg["primary_candidate"],
            "scale_feature": cfg["primary_scale_feature"],
            "horizons": cfg["horizons_sessions"],
            "quantiles": cfg["quantiles"],
        },
        "predeclared_diagnostics": {
            "exact_source_reproduction": True,
            "tail_specific_pinball_and_calibration": cfg["quantiles"],
            "asset_concentration": True,
            "sector_concentration": True,
            "leave_one_asset_out": True,
            "leave_one_sector_out": True,
            "volatility_regimes": {
                "defined_from_each_fold_training_scale_only": True,
                "train_quantiles": cfg["volatility_regime_train_quantiles"],
            },
            "calibration_drift": {
                "nonoverlapping_blocks_within_each_outer_fold": True,
                "origin_days_per_block": cfg["calibration_block_origin_days"],
            },
            "secondary_asset_empirical_direct_comparison": True,
            "alternative_causal_scales": cfg["alternative_scale_features"],
        },
        "resampling": {
            "primary_unit": "origin_trading_day",
            "global_and_asset_reference": {
                "moving_block_lengths_origin_days": cfg["moving_block_lengths_origin_days"],
                "bootstrap_reps": cfg["bootstrap_reps"],
            },
            "secondary_diagnostics": {
                "block_length_origin_days": cfg["diagnostic_block_length_origin_days"],
                "bootstrap_reps": cfg["diagnostic_bootstrap_reps"],
            },
            "seed": cfg["bootstrap_seed"],
        },
        "claim_boundaries": {
            "diagnostics_may_narrow_or_falsify_v006": True,
            "alternative_scales_are_not_candidate_selection": True,
            "no_new_model_training": True,
            "no_directional_alpha_claim": True,
            "not_a_path_model": True,
            "not_strict_historical_pit": True,
            "not_production_ready": True,
        },
        "required_source_artifacts": required_source,
        "missing": missing,
    }


def benchmark(
    config_path: Path,
    core_db: Path,
    source_report_dir: Path,
    output_dir: Path,
    horizons: list[int],
) -> dict[str, Any]:
    cfg = load_config(config_path)
    result: dict[str, Any] = {
        "robustness_version": cfg["version"],
        "horizons": {},
    }
    for horizon in horizons:
        report, tables = run_horizon_robustness(
            core_db,
            int(horizon),
            cfg,
            source_report_dir,
        )
        _json_write(output_dir / f"h{int(horizon)}_robustness.json", report)
        for name, table in tables.items():
            table.to_csv(
                output_dir / f"h{int(horizon)}_{name}.csv",
                index=False,
            )
        result["horizons"][str(horizon)] = {
            "source_reproduction": report["source_reproduction"],
            "completed_v006_primary_reanalysis": report[
                "completed_v006_primary_reanalysis"
            ],
            "secondary_candidate_vs_asset_reference": report[
                "secondary_candidate_vs_asset_reference"
            ],
            "concentration": report["concentration"],
            "calibration_drift": report["calibration_drift"],
        }
    return result


def summary(config_path: Path, output_dir: Path) -> dict[str, Any]:
    cfg = load_config(config_path)
    missing = []
    reports = {}
    for horizon in cfg["horizons_sessions"]:
        path = output_dir / f"h{int(horizon)}_robustness.json"
        if not path.exists():
            missing.append(str(path))
            continue
        reports[str(horizon)] = json.loads(path.read_text(encoding="utf-8"))
    if missing:
        raise FileNotFoundError(
            "all four V006.1 horizon reports are required before summary: "
            + ", ".join(missing)
        )

    matrix = {}
    all_reproduced = True
    for horizon in cfg["horizons_sessions"]:
        report = reports[str(horizon)]
        all_reproduced &= report["source_reproduction"]["status"] == "PASS"
        q05 = report["tail_specific"]["q05"]
        q95 = report["tail_specific"]["q95"]
        matrix[str(horizon)] = {
            "v006_vs_global_daily_delta_pct": report[
                "completed_v006_primary_reanalysis"
            ]["origin_day_equal_weight_delta_pct"],
            "v006_vs_asset_daily_delta_pct": report[
                "secondary_candidate_vs_asset_reference"
            ]["origin_day_equal_weight_delta_pct"],
            "q05_v006_vs_global_daily_delta_pct": q05[
                "candidate_vs_global"
            ]["origin_day_equal_weight_delta_pct"],
            "q95_v006_vs_global_daily_delta_pct": q95[
                "candidate_vs_global"
            ]["origin_day_equal_weight_delta_pct"],
            "low_vol_daily_delta_pct": report["volatility_regimes"]["low"][
                "candidate_vs_global"
            ]["origin_day_equal_weight_delta_pct"],
            "mid_vol_daily_delta_pct": report["volatility_regimes"]["mid"][
                "candidate_vs_global"
            ]["origin_day_equal_weight_delta_pct"],
            "high_vol_daily_delta_pct": report["volatility_regimes"]["high"][
                "candidate_vs_global"
            ]["origin_day_equal_weight_delta_pct"],
            "top10_asset_abs_contribution_share": report["concentration"]["asset"][
                "top_k_absolute_contribution_share"
            ]["10"],
            "leave_one_asset_out_min_delta_pct": report["concentration"]["asset"][
                "leave_one_asset_out_min_primary_delta_pct"
            ],
            "leave_one_sector_out_min_delta_pct": report["concentration"]["sector"][
                "leave_one_sector_out_min_primary_delta_pct"
            ],
            "max_abs_central50_calibration_error_by_block": report[
                "calibration_drift"
            ]["candidate_max_abs_central50_coverage_error"],
            "max_abs_central90_calibration_error_by_block": report[
                "calibration_drift"
            ]["candidate_max_abs_central90_coverage_error"],
            "alternative_scales": {
                feature: {
                    "vs_global_daily_delta_pct": report["alternative_causal_scales"][feature][
                        "candidate_vs_global"
                    ]["origin_day_equal_weight_delta_pct"],
                    "alternative_vs_v006_daily_delta_pct": report[
                        "alternative_causal_scales"
                    ][feature]["alternative_vs_v006"]["origin_day_equal_weight_delta_pct"],
                }
                for feature in cfg["alternative_scale_features"]
            },
        }
    payload = {
        "robustness_version": cfg["version"],
        "status": "COMPLETE" if all_reproduced else "INVALID_SOURCE_REPRODUCTION",
        "source_v006_reproduced_all_horizons": bool(all_reproduced),
        "headline_matrix": matrix,
        "interpretation_rule": (
            "Do not auto-promote or replace V006 from this summary. Review concentration, "
            "tail asymmetry, regime dependence, calibration drift and the direct asset-empirical "
            "comparison before preregistering any learned distributional model."
        ),
        "claim_boundary": cfg["claim_boundary"],
    }
    _json_write(output_dir / "robustness_summary.json", payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--stage",
        choices=("plan", "benchmark", "summary"),
        required=True,
    )
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--core-db", type=Path, default=DEFAULT_CORE_DB)
    parser.add_argument(
        "--source-report-dir",
        type=Path,
        default=DEFAULT_SOURCE_REPORT_DIR,
    )
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--horizons", default="1,3,5,10")
    args = parser.parse_args()

    if args.stage == "plan":
        payload = plan(args.config, args.core_db, args.source_report_dir)
        _json_write(args.output_dir / "preregistration.json", payload)
    elif args.stage == "benchmark":
        cfg = load_config(args.config)
        horizons = _parse_horizons(args.horizons, [int(x) for x in cfg["horizons_sessions"]])
        payload = benchmark(
            args.config,
            args.core_db,
            args.source_report_dir,
            args.output_dir,
            horizons,
        )
    else:
        payload = summary(args.config, args.output_dir)
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()

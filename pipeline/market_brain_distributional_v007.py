from __future__ import annotations

import argparse
import json
from pathlib import Path
from typing import Any

from evaluation.market.distributional_v007 import (
    DEFAULT_CONFIG,
    DEFAULT_CORE_DB,
    load_config,
    run_horizon,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_OUTPUT_DIR = (
    ROOT / "reports" / "market_brain_distributional_v007" / "adaptive_tail_v001"
)
DEFAULT_V0061_SUMMARY = (
    ROOT
    / "reports"
    / "market_brain_distributional_v0061"
    / "robustness_v001"
    / "robustness_summary.json"
)


def _json_write(path: Path, payload: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _parse_horizons(text: str, allowed: list[int]) -> list[int]:
    result = [int(x.strip()) for x in text.split(",") if x.strip()]
    if not result:
        raise ValueError("at least one horizon required")
    if len(set(result)) != len(result):
        raise ValueError("duplicate horizons")
    invalid = sorted(set(result) - set(allowed))
    if invalid:
        raise ValueError(f"unsupported horizons: {invalid}")
    return result


def plan(
    config_path: Path,
    core_db: Path,
    v0061_summary: Path,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    missing = []
    if not core_db.exists():
        missing.append(str(core_db))
    if not v0061_summary.exists():
        missing.append(str(v0061_summary))
        source_ok = False
        source_payload = None
    else:
        source_payload = json.loads(v0061_summary.read_text(encoding="utf-8"))
        source_ok = bool(
            source_payload.get("status") == "COMPLETE"
            and source_payload.get("source_v006_reproduced_all_horizons") is True
        )
        if not source_ok:
            missing.append("valid completed V006.1 source reproduction")
    return {
        "status": "PASS" if not missing and source_ok else "FAIL",
        "benchmark_version": cfg["version"],
        "model_version": cfg["model_version"],
        "frozen_question": (
            "Can a nested-selected, asset-anchored, asymmetric and sub/super-linear "
            "volatility response improve calibrated terminal-return quantiles beyond the "
            "strong vol63 empirical scale reference without learning direction?"
        ),
        "mathematical_contract": {
            "q50": "global train median only",
            "asset_tail_anchor": cfg["asset_tail_anchor"],
            "lower_and_upper_parameters_selected_separately": True,
            "dynamic_scale_formula": cfg["scale_formula"],
            "scale_features": cfg["scale_features"],
            "alpha_grid": cfg["alpha_grid"],
            "lambda20_grid": cfg["lambda20_grid"],
            "kappa_grid": cfg["kappa_grid"],
        },
        "selection_contract": {
            "nested_temporal_validation": True,
            "inner_validation_fraction": cfg["inner_validation_fraction"],
            "selection_score": cfg["selection_score"],
            "outer_primary_score": cfg["primary_score"],
            "all_horizons_required": cfg["horizons_sessions"],
        },
        "controls": {
            "primary_reference": cfg["primary_reference"],
            "secondary_references": cfg["secondary_references"],
        },
        "claim_boundaries": {
            "informed_by_v0061": True,
            "not_independent_prospective_confirmation": True,
            "no_directional_alpha_claim": True,
            "no_event_graph_macro_external_context": True,
            "not_a_path_model": True,
            "not_production_ready": True,
        },
        "v0061_summary": str(v0061_summary),
        "v0061_source_valid": bool(source_ok),
        "missing": missing,
    }


def benchmark(
    config_path: Path,
    core_db: Path,
    output_dir: Path,
    horizons: list[int],
) -> dict[str, Any]:
    cfg = load_config(config_path)
    payload: dict[str, Any] = {
        "benchmark_version": cfg["version"],
        "horizons": {},
    }
    for horizon in horizons:
        report, _, tables = run_horizon(core_db, int(horizon), cfg)
        _json_write(output_dir / f"h{int(horizon)}_benchmark.json", report)
        for name, table in tables.items():
            table.to_csv(output_dir / f"h{int(horizon)}_{name}.csv", index=False)
        payload["horizons"][str(horizon)] = {
            "horizon_gate": report["horizon_gate"],
            "primary_comparison": report["primary_comparison"],
            "fold_selected_parameters": [
                {
                    "fold_id": row["fold_id"],
                    "nested_selection": row["nested_selection"],
                }
                for row in report["fold_results"]
            ],
        }
    return payload


def summary(config_path: Path, output_dir: Path) -> dict[str, Any]:
    cfg = load_config(config_path)
    reports = {}
    missing = []
    for horizon in cfg["horizons_sessions"]:
        path = output_dir / f"h{int(horizon)}_benchmark.json"
        if not path.exists():
            missing.append(str(path))
        else:
            reports[str(horizon)] = json.loads(path.read_text(encoding="utf-8"))
    if missing:
        raise FileNotFoundError(
            "all V007 horizon reports required before summary: " + ", ".join(missing)
        )
    matrix = {}
    strong = 0
    positive_points = 0
    failed = 0
    for horizon in cfg["horizons_sessions"]:
        report = reports[str(horizon)]
        gate = report["horizon_gate"]
        if gate["status"] == "PASS_STRONG":
            strong += 1
        if float(gate["primary_point_delta_pct"]) > 0.0:
            positive_points += 1
        if gate["status"] == "FAIL":
            failed += 1
        fold_params = [x["nested_selection"] for x in report["fold_results"]]
        matrix[str(horizon)] = {
            "gate": gate,
            "candidate_vs_vol63_daily_delta_pct": report["primary_comparison"][
                "origin_day_equal_weight_delta_pct"
            ],
            "candidate_vs_vol20_daily_delta_pct": report["secondary_comparisons"][
                "vol20_scaled_empirical"
            ]["origin_day_equal_weight_delta_pct"],
            "candidate_vs_asset_daily_delta_pct": report["secondary_comparisons"][
                "asset_empirical"
            ]["origin_day_equal_weight_delta_pct"],
            "candidate_vs_global_daily_delta_pct": report["secondary_comparisons"][
                "train_empirical"
            ]["origin_day_equal_weight_delta_pct"],
            "q05_vs_vol63_daily_delta_pct": report[
                "tail_specific_vs_primary_reference"
            ]["q05"]["origin_day_equal_weight_delta_pct"],
            "q95_vs_vol63_daily_delta_pct": report[
                "tail_specific_vs_primary_reference"
            ]["q95"]["origin_day_equal_weight_delta_pct"],
            "selected_parameters_by_fold": [
                {
                    "lower": x["lower_selected"],
                    "upper": x["upper_selected"],
                }
                for x in fold_params
            ],
        }
    if strong >= 3 and failed == 0:
        overall = "MULTI_HORIZON_CANDIDATE"
    elif positive_points >= 3 and failed <= 1:
        overall = "DEVELOPMENTALLY_INTERESTING_NOT_PROMOTED"
    else:
        overall = "NO_MULTI_HORIZON_PROMOTION"
    payload = {
        "benchmark_version": cfg["version"],
        "status": "COMPLETE",
        "overall_interpretation": overall,
        "strong_horizon_gates": strong,
        "positive_point_horizons": positive_points,
        "failed_horizons": failed,
        "headline_matrix": matrix,
        "promotion_boundary": (
            "This benchmark is developmental because its mathematical hypothesis was informed by "
            "V006.1 outcomes. Even a strong result is not independent prospective confirmation."
        ),
    }
    _json_write(output_dir / "benchmark_summary.json", payload)
    return payload


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--stage", choices=("plan", "benchmark", "summary"), required=True)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--core-db", type=Path, default=DEFAULT_CORE_DB)
    parser.add_argument("--v0061-summary", type=Path, default=DEFAULT_V0061_SUMMARY)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--horizons", default="1,3,5,10")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.stage == "plan":
        payload = plan(args.config, args.core_db, args.v0061_summary)
        _json_write(args.output_dir / "preregistration.json", payload)
    elif args.stage == "benchmark":
        requested = _parse_horizons(args.horizons, list(cfg["horizons_sessions"]))
        payload = benchmark(args.config, args.core_db, args.output_dir, requested)
    else:
        payload = summary(args.config, args.output_dir)
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()

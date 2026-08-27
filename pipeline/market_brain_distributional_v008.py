from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from evaluation.market.distributional_v008 import run_horizon
from models.market.distributional_v008_conditional_quantiles import (
    DEFAULT_CONFIG,
    DEFAULT_CORE_DB,
    load_config,
    resolve_feature_manifest,
    validate_feature_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_V0061_SUMMARY = ROOT / "reports" / "market_brain_distributional_v0061" / "robustness_v001" / "robustness_summary.json"
DEFAULT_V007_SUMMARY = ROOT / "reports" / "market_brain_distributional_v007" / "adaptive_tail_v0011" / "benchmark_summary.json"
DEFAULT_OUTPUT_DIR = ROOT / "reports" / "market_brain_distributional_v008" / "conditional_residual_quantiles_v0011"


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n", encoding="utf-8")


def _sha(payload: Any) -> str:
    raw = json.dumps(payload, sort_keys=True, separators=(",", ":"), allow_nan=False).encode()
    return hashlib.sha256(raw).hexdigest()


def _parse_horizons(text: str, allowed: list[int]) -> list[int]:
    values = [int(x.strip()) for x in text.split(",") if x.strip()]
    if not values or any(v not in allowed for v in values):
        raise ValueError(f"horizons must be subset of {allowed}")
    return values


def _source_valid(v0061_path: Path, v007_path: Path) -> tuple[bool, list[str]]:
    problems = []
    if not v0061_path.exists():
        problems.append(f"missing {v0061_path}")
    else:
        p = json.loads(v0061_path.read_text(encoding="utf-8"))
        if not (p.get("status") == "COMPLETE" and p.get("source_v006_reproduced_all_horizons") is True):
            problems.append("V006.1 source reproduction invalid")
    if not v007_path.exists():
        problems.append(f"missing {v007_path}")
    else:
        p = json.loads(v007_path.read_text(encoding="utf-8"))
        if not (
            p.get("status") == "COMPLETE"
            and p.get("overall_interpretation") == "NO_MULTI_HORIZON_PROMOTION"
            and int(p.get("positive_point_horizons", -1)) == 0
        ):
            problems.append("V007 must be complete and rejected before V008")
    return not problems, problems


def _feature_null_audit(core_db: Path, cfg: dict[str, Any], manifest: dict[str, Any]) -> dict[str, Any]:
    features = list(manifest["full_endogenous"])
    out = {}
    with sqlite3.connect(core_db) as conn:
        for name in features:
            row = conn.execute(
                f"""
                SELECT COUNT(*), SUM(CASE WHEN s.{name} IS NULL THEN 1 ELSE 0 END)
                FROM market_daily_v003_states s
                WHERE s.feature_version=?
                """,
                (cfg["market_feature_version"],),
            ).fetchone()
            total = int(row[0] or 0)
            missing = int(row[1] or 0)
            out[name] = {"rows": total, "null_rows": missing, "null_fraction": (missing / total if total else 1.0)}
    return out




def _temporal_feasibility_audit(core_db: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    """Schema/clock-only audit. Reads no return outcomes and scores no model."""
    out: dict[str, Any] = {}
    with sqlite3.connect(core_db) as conn:
        for h in cfg["horizons_sessions"]:
            rows = conn.execute(
                """
                SELECT DISTINCT l.origin_trading_day, l.target_trading_day
                FROM market_daily_v003_labels l
                JOIN market_daily_v003_states s ON s.state_id=l.state_id
                WHERE l.horizon_sessions=?
                  AND l.label_status='usable'
                  AND l.label_version=?
                  AND s.feature_version=?
                ORDER BY l.origin_trading_day
                """,
                (int(h), str(cfg["label_version"]), str(cfg["market_feature_version"])),
            ).fetchall()
            origin_days = sorted({str(r[0]) for r in rows})
            n_days = len(origin_days)
            nominal_initial_outer_train_days = int(math.floor(n_days * float(cfg["initial_fraction"])))
            # Conservative clock budget: one horizon-sized purge before the calibration
            # boundary and another before the nested validation boundary.
            required_days_conservative = (
                int(cfg["recent_calibration_origin_days"])
                + int(cfg["minimum_inner_validation_origin_days"])
                + int(cfg["minimum_inner_train_origin_days"])
                + 2 * int(h)
            )
            out[str(h)] = {
                "usable_origin_days": n_days,
                "nominal_initial_outer_train_days": nominal_initial_outer_train_days,
                "recent_calibration_origin_days": int(cfg["recent_calibration_origin_days"]),
                "minimum_inner_validation_origin_days": int(cfg["minimum_inner_validation_origin_days"]),
                "minimum_inner_train_origin_days": int(cfg["minimum_inner_train_origin_days"]),
                "conservative_purge_budget_origin_days": 2 * int(h),
                "required_days_conservative": required_days_conservative,
                "margin_days": nominal_initial_outer_train_days - required_days_conservative,
                "status": "PASS" if nominal_initial_outer_train_days >= required_days_conservative else "FAIL",
            }
    return out

def plan(config_path: Path, core_db: Path, v0061_summary: Path, v007_summary: Path, output_dir: Path) -> dict[str, Any]:
    cfg = load_config(config_path)
    missing = []
    if not core_db.exists():
        missing.append(f"missing {core_db}")
        manifest = None
        null_audit = None
        temporal_feasibility = None
    else:
        manifest = resolve_feature_manifest(core_db, cfg)
        try:
            validate_feature_manifest(manifest, cfg)
        except Exception as exc:
            missing.append(str(exc))
        null_audit = _feature_null_audit(core_db, cfg, manifest)
        if any(v["null_fraction"] > 0.20 for v in null_audit.values()):
            missing.append("one or more resolved features exceed 20% missing; review before preregistration")
        temporal_feasibility = _temporal_feasibility_audit(core_db, cfg)
        if any(v["status"] != "PASS" for v in temporal_feasibility.values()):
            missing.append("nested temporal split is infeasible for one or more horizons under the conservative clock audit")
    source_ok, source_problems = _source_valid(v0061_summary, v007_summary)
    missing.extend(source_problems)
    manifest_sha = None
    if manifest is not None:
        manifest_sha = _sha(manifest)
        _write(output_dir / "resolved_feature_manifest.json", manifest)
    payload = {
        "status": "PASS" if not missing and source_ok else "FAIL",
        "benchmark_version": cfg["version"],
        "model_version": cfg["model_version"],
        "frozen_question": "After conditioning terminal returns on the strongest empirical vol63 scale and giving both candidate and reference the same recent train-only recalibration, does the current endogenous causal Market State X_t contain reproducible incremental information about the standardized return distribution?",
        "professional_investor_interpretation": {
            "what_current_X_contains": "price/volume path, realized volatility, drawdown/range and endogenous cross-sectional/sector context resolved from the frozen Core V003 state",
            "what_current_X_does_not_claim_to_contain": "analyst expectations/revisions, option-implied distribution, positioning/flows, fundamentals/valuation, rich event surprise, causal macro vintages or graph propagation",
            "failure_meaning": "do not respond with more model capacity; treat as evidence that information state may be insufficient and enrich information causally in a later preregistered experiment",
        },
        "target_decomposition": "z=(return_pct-development_train_median)/asset_vol_63d_pct for positive scale; predict conditional quantiles of z; reconstruct return quantiles; nonpositive scale uses empirical fallback",
        "anti_selection_design": {
            "outer_purged_expanding_folds": cfg["outer_folds"],
            "recent_calibration_origin_days": cfg["recent_calibration_origin_days"],
            "nested_profile_selection": True,
            "feature_family_primary_fixed": "full_endogenous",
            "scale_only_and_own_state_are_diagnostics_only": True,
            "posthoc_rescue_forbidden": True,
        },
        "fair_reference": {
            "primary": cfg["primary_reference"],
            "reason": "V006.1 exposed calibration drift; the vol63 reference receives the same recent train-only standardized quantile calibration as the candidate so V008 cannot win merely by recalibrating recency",
            "raw_v006_controls_retained": True,
        },
        "resolved_feature_manifest": manifest,
        "resolved_feature_manifest_sha256": manifest_sha,
        "feature_null_audit": null_audit,
        "temporal_split_feasibility": temporal_feasibility,
        "preperformance_amendment": cfg.get("preperformance_amendment"),
        "source_v0061": str(v0061_summary),
        "source_v007": str(v007_summary),
        "source_valid": source_ok,
        "missing": missing,
    }
    _write(output_dir / "preregistration.json", payload)
    return payload


def _load_manifest(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "resolved_feature_manifest.json"
    if not path.exists():
        raise FileNotFoundError("run --stage plan and freeze resolved_feature_manifest.json before benchmark")
    return json.loads(path.read_text(encoding="utf-8"))


def benchmark(config_path: Path, core_db: Path, output_dir: Path, horizons: list[int]) -> dict[str, Any]:
    cfg = load_config(config_path)
    manifest = _load_manifest(output_dir)
    current = resolve_feature_manifest(core_db, cfg)
    if _sha(current) != _sha(manifest):
        raise RuntimeError("Core V003 feature schema changed after V008 plan; do not benchmark")
    payload = {"benchmark_version": cfg["version"], "horizons": {}}
    for h in horizons:
        report, _, tables = run_horizon(core_db, int(h), cfg, manifest)
        _write(output_dir / f"h{int(h)}_benchmark.json", report)
        for name, table in tables.items():
            table.to_csv(output_dir / f"h{int(h)}_{name}.csv", index=False)
        payload["horizons"][str(h)] = {
            "horizon_gate": report["horizon_gate"],
            "primary_comparison": report["primary_comparison"],
            "information_controls": report["information_controls"],
            "vol63_recent_recalibration_vs_raw": report["vol63_recent_recalibration_vs_raw"],
        }
    return payload


def summary(config_path: Path, output_dir: Path) -> dict[str, Any]:
    cfg = load_config(config_path)
    reports = {}
    for h in cfg["horizons_sessions"]:
        p = output_dir / f"h{int(h)}_benchmark.json"
        if not p.exists():
            raise FileNotFoundError(f"missing H{h} benchmark")
        reports[str(h)] = json.loads(p.read_text(encoding="utf-8"))
    matrix = {}
    strong = 0
    significant_fail = 0
    positive = 0
    full_beats_scale_points = 0
    full_beats_own_points = 0
    for h in cfg["horizons_sessions"]:
        r = reports[str(h)]
        gate = r["horizon_gate"]
        strong += int(gate["status"] == "PASS_STRONG")
        significant_fail += int(gate["status"] == "FAIL_SIGNIFICANT")
        positive += int(float(gate["primary_point_delta_pct"]) > 0.0)
        scale_diag = r["information_controls"]["hgb_scale_only_calibrated"]
        own_diag = r["information_controls"]["hgb_own_state_calibrated"]
        full_vs_scale = float(scale_diag["full_candidate_vs_this_control"]["origin_day_equal_weight_delta_pct"])
        full_vs_own = float(own_diag["full_candidate_vs_this_control"]["origin_day_equal_weight_delta_pct"])
        full_beats_scale_points += int(full_vs_scale > 0)
        full_beats_own_points += int(full_vs_own > 0)
        matrix[str(h)] = {
            "gate": gate,
            "candidate_vs_calibrated_vol63": r["primary_comparison"]["origin_day_equal_weight_delta_pct"],
            "candidate_vs_raw_vol63": r["secondary_comparisons"]["vol63_raw"]["origin_day_equal_weight_delta_pct"],
            "candidate_vs_raw_vol20": r["secondary_comparisons"]["vol20_raw"]["origin_day_equal_weight_delta_pct"],
            "candidate_vs_asset_empirical": r["secondary_comparisons"]["asset_empirical"]["origin_day_equal_weight_delta_pct"],
            "candidate_vs_global_empirical": r["secondary_comparisons"]["train_empirical"]["origin_day_equal_weight_delta_pct"],
            "full_vs_scale_only": full_vs_scale,
            "full_vs_own_state": full_vs_own,
            "vol63_recalibration_gain_vs_raw": r["vol63_recent_recalibration_vs_raw"]["origin_day_equal_weight_delta_pct"],
            "selected_profiles": [x["selected_profile"] for x in r["fold_results"]],
        }
    if strong >= 3 and significant_fail == 0:
        interpretation = "ENDOGENOUS_CONDITIONAL_INFORMATION_SUPPORTED"
    elif strong == 0 and positive <= 1:
        interpretation = "CURRENT_ENDOGENOUS_INFORMATION_INSUFFICIENT_BEYOND_CALIBRATED_VOL63"
    else:
        interpretation = "MIXED_HORIZON_ENDOGENOUS_INFORMATION"
    payload = {
        "benchmark_version": cfg["version"],
        "status": "COMPLETE",
        "overall_interpretation": interpretation,
        "strong_horizon_gates": strong,
        "significant_fail_horizons": significant_fail,
        "positive_point_horizons": positive,
        "full_beats_scale_only_point_horizons": full_beats_scale_points,
        "full_beats_own_state_point_horizons": full_beats_own_points,
        "headline_matrix": matrix,
        "decision_rule": {
            "if_supported": "retain learned endogenous Market Brain and only then test incremental new information blocks",
            "if_insufficient": "stop increasing endogenous learner capacity; next experiment must add a causally versioned information class such as expectations/options/fundamentals/event surprise, one block at a time",
            "if_mixed": "diagnose horizon-specific information before new model capacity; no best-horizon promotion",
        },
    }
    _write(output_dir / "benchmark_summary.json", payload)
    return payload


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=("plan", "benchmark", "summary"), required=True)
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--core-db", type=Path, default=DEFAULT_CORE_DB)
    p.add_argument("--v0061-summary", type=Path, default=DEFAULT_V0061_SUMMARY)
    p.add_argument("--v007-summary", type=Path, default=DEFAULT_V007_SUMMARY)
    p.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    p.add_argument("--horizons", default="1,3,5,10")
    args = p.parse_args()
    cfg = load_config(args.config)
    if args.stage == "plan":
        payload = plan(args.config, args.core_db, args.v0061_summary, args.v007_summary, args.output_dir)
    elif args.stage == "benchmark":
        payload = benchmark(args.config, args.core_db, args.output_dir, _parse_horizons(args.horizons, list(cfg["horizons_sessions"])))
    else:
        payload = summary(args.config, args.output_dir)
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()

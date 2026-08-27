from __future__ import annotations

import argparse
import hashlib
import json
import math
import sqlite3
from pathlib import Path
from typing import Any

from evaluation.market.distributional_v0081 import run_horizon
from models.market.distributional_v0081_endogenous_closure import (
    DEFAULT_CONFIG,
    DEFAULT_CORE_DB,
    load_config,
    resolve_feature_manifest,
    validate_frozen_manifest,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_V008_DIR = (
    ROOT
    / "reports"
    / "market_brain_distributional_v008"
    / "conditional_residual_quantiles_v0011"
)
DEFAULT_V008_SUMMARY = DEFAULT_V008_DIR / "benchmark_summary.json"
DEFAULT_V008_H1 = DEFAULT_V008_DIR / "h1_benchmark.json"
DEFAULT_V008_MANIFEST = DEFAULT_V008_DIR / "resolved_feature_manifest.json"
DEFAULT_OUTPUT_DIR = (
    ROOT
    / "reports"
    / "market_brain_distributional_v0081"
    / "endogenous_closure_v001"
)


def _write(path: Path, payload: Any) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(payload, indent=2, sort_keys=True, allow_nan=False) + "\n",
        encoding="utf-8",
    )


def _sha(payload: Any) -> str:
    raw = json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    ).encode()
    return hashlib.sha256(raw).hexdigest()


def _parse_horizons(text: str, allowed: list[int]) -> list[int]:
    values = [int(value.strip()) for value in text.split(",") if value.strip()]
    if not values or len(values) != len(set(values)):
        raise ValueError("horizons must be a nonempty unique list")
    if any(value not in allowed for value in values):
        raise ValueError(f"horizons must be a subset of {allowed}")
    return values


def _load_json(path: Path) -> dict[str, Any]:
    return json.loads(path.read_text(encoding="utf-8"))


def _validate_v008_sources(
    cfg: dict[str, Any],
    summary_path: Path,
    h1_path: Path,
    manifest_path: Path,
) -> tuple[dict[str, Any], list[str]]:
    problems = []
    for path in (summary_path, h1_path, manifest_path):
        if not path.exists():
            problems.append(f"missing {path}")
    if problems:
        return {}, problems

    summary = _load_json(summary_path)
    h1 = _load_json(h1_path)
    manifest = _load_json(manifest_path)
    expected_version = cfg["source_v008_version"]
    if summary.get("benchmark_version") != expected_version:
        problems.append("V008 summary version mismatch")
    if h1.get("benchmark_version") != expected_version:
        problems.append("V008 H1 version mismatch")
    if summary.get("status") != "COMPLETE":
        problems.append("V008 summary is not complete")
    if int(summary.get("strong_horizon_gates", -1)) != 0:
        problems.append("V008 unexpectedly has a strong horizon gate")
    if int(summary.get("significant_fail_horizons", -1)) != 4:
        problems.append("V008 must retain four significant primary failures")
    if h1.get("horizon_gate", {}).get("status") != "FAIL_SIGNIFICANT":
        problems.append("V008 H1 primary gate changed")

    try:
        own_vs_calibrated = float(
            h1["information_controls"]["hgb_own_state_calibrated"][
                "vs_primary_reference"
            ]["origin_day_equal_weight_delta_pct"]
        )
        raw_vs_calibrated = float(
            h1["vol63_recent_recalibration_vs_raw"][
                "origin_day_equal_weight_delta_pct"
            ]
        )
        raw_vs_own = own_vs_calibrated + raw_vs_calibrated
    except (KeyError, TypeError, ValueError):
        problems.append("cannot derive V008 H1 own-state vs raw-vol63 point comparison")
        own_vs_calibrated = None
        raw_vs_calibrated = None
        raw_vs_own = None
    if raw_vs_own is not None and raw_vs_own <= 0.0:
        problems.append("V008 no longer contains the positive H1 own-state point ambiguity")

    if list(manifest.get("own_state", [])) != list(cfg["frozen_own_features"]):
        problems.append("V008 source own-state manifest differs from V008.1 frozen features")
    if list(manifest.get("scale_only", [])) != list(
        cfg["capacity_placebo"]["preserved_aligned_features"]
    ):
        problems.append("V008 source scale-only manifest differs from placebo-preserved features")

    return {
        "summary_sha256": _sha(summary),
        "h1_report_sha256": _sha(h1),
        "manifest_sha256": _sha(manifest),
        "derived_h1_comparison": {
            "calibrated_vol63_minus_calibrated_own_state_pinball_pct": own_vs_calibrated,
            "raw_vol63_minus_calibrated_vol63_pinball_pct": raw_vs_calibrated,
            "raw_vol63_minus_calibrated_own_state_pinball_pct": raw_vs_own,
            "derivation": "(calibrated_vol63-own_state)+(raw_vol63-calibrated_vol63)",
            "status": "posthoc_motivation_only_not_confirmatory_evidence",
        },
    }, problems


def _feature_null_audit(
    core_db: Path,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    features = list(cfg["frozen_own_features"])
    expressions = ", ".join(
        f"SUM(CASE WHEN {name} IS NULL THEN 1 ELSE 0 END)"
        for name in features
    )
    with sqlite3.connect(core_db) as conn:
        row = conn.execute(
            f"""
            SELECT COUNT(*), {expressions}
            FROM market_daily_v003_states
            WHERE feature_version=?
            """,
            (cfg["market_feature_version"],),
        ).fetchone()
    total = int(row[0] or 0)
    return {
        name: {
            "rows": total,
            "null_rows": int(row[index + 1] or 0),
            "null_fraction": (
                float(int(row[index + 1] or 0) / total)
                if total
                else 1.0
            ),
        }
        for index, name in enumerate(features)
    }


def _outer_split_feasibility_audit(
    core_db: Path,
    cfg: dict[str, Any],
) -> dict[str, Any]:
    out = {}
    with sqlite3.connect(core_db) as conn:
        for horizon in cfg["horizons_sessions"]:
            rows = conn.execute(
                """
                SELECT DISTINCT l.origin_trading_day
                FROM market_daily_v003_labels l
                JOIN market_daily_v003_states s ON s.state_id=l.state_id
                WHERE l.horizon_sessions=?
                  AND l.label_status='usable'
                  AND l.label_version=?
                  AND s.feature_version=?
                ORDER BY l.origin_trading_day
                """,
                (
                    int(horizon),
                    str(cfg["label_version"]),
                    str(cfg["market_feature_version"]),
                ),
            ).fetchall()
            days = len(rows)
            initial = int(math.floor(days * float(cfg["initial_fraction"])))
            required = int(cfg["minimum_outer_train_origin_days"]) + int(horizon)
            out[str(horizon)] = {
                "usable_origin_days": days,
                "nominal_initial_outer_train_days": initial,
                "minimum_outer_train_origin_days": int(
                    cfg["minimum_outer_train_origin_days"]
                ),
                "conservative_purge_budget_origin_days": int(horizon),
                "required_days_conservative": required,
                "margin_days": initial - required,
                "status": "PASS" if initial >= required else "FAIL",
            }
    return out


def plan(
    config_path: Path,
    core_db: Path,
    v008_summary: Path,
    v008_h1: Path,
    v008_manifest: Path,
    output_dir: Path,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    missing = []
    source_audit, source_problems = _validate_v008_sources(
        cfg,
        v008_summary,
        v008_h1,
        v008_manifest,
    )
    missing.extend(source_problems)

    manifest = None
    null_audit = None
    split_audit = None
    if not core_db.exists():
        missing.append(f"missing {core_db}")
    else:
        actual_size = core_db.stat().st_size
        if actual_size != int(cfg["source_core_db_size_bytes"]):
            missing.append(
                "Core V003 DB size differs from frozen source; verify before benchmarking"
            )
        manifest = resolve_feature_manifest(core_db, cfg)
        try:
            validate_frozen_manifest(manifest, cfg)
        except Exception as exc:
            missing.append(str(exc))
        null_audit = _feature_null_audit(core_db, cfg)
        if any(value["null_fraction"] > 0.20 for value in null_audit.values()):
            missing.append("one or more frozen own-state features exceed 20% missing")
        split_audit = _outer_split_feasibility_audit(core_db, cfg)
        if any(value["status"] != "PASS" for value in split_audit.values()):
            missing.append("outer temporal split is infeasible for one or more horizons")

    manifest_sha = None
    if manifest is not None:
        manifest_sha = _sha(manifest)
        _write(output_dir / "resolved_feature_manifest.json", manifest)

    payload = {
        "status": "PASS" if not missing else "FAIL",
        "benchmark_version": cfg["version"],
        "model_version": cfg["model_version"],
        "frozen_question": "Without recent post-model recalibration or cross-sectional/sector context, does the exact V008 own price/volume state improve H1 standardized-return quantiles beyond raw vol63?",
        "reason_for_experiment": {
            "V008_primary_result": "full endogenous calibrated HGB failed significantly at H1/H3/H5/H10",
            "remaining_ambiguity": "the V008 H1 own-state diagnostic had a tiny positive derived point versus raw vol63 but no direct preregistered bootstrap and retained the harmful calibration/development split",
            "purpose": "close the current endogenous engineering branch or justify only a fresh temporal confirmation",
        },
        "mathematical_contract": {
            "positive_scale_target": "z=(return_pct-outer_train_median)/asset_vol_63d_pct",
            "candidate": "fixed shallow HGB quantiles of z conditional on the frozen 14-feature own state, fit on complete purged outer train",
            "reference": "outer-train empirical z quantiles reconstructed with prediction-time asset_vol_63d_pct",
            "post_model_calibration": "none",
            "nonpositive_scale": "outer-train unconditional empirical return quantiles",
        },
        "anti_selection_design": {
            "primary_horizon": int(cfg["primary_horizon_sessions"]),
            "secondary_horizons": [3, 5, 10],
            "secondary_horizons_cannot_rescue_H1": True,
            "fixed_profile_no_selection": cfg["fixed_model_profile"],
            "frozen_own_features": cfg["frozen_own_features"],
            "capacity_placebo": cfg["capacity_placebo"],
            "historical_sample_reused_after_V008": True,
            "fresh_untouched_holdout_required_for_promotion": True,
        },
        "primary_gate": cfg["primary_gate"],
        "source_v008_audit": source_audit,
        "source_v008_summary": str(v008_summary),
        "source_v008_h1": str(v008_h1),
        "source_v008_manifest": str(v008_manifest),
        "resolved_feature_manifest": manifest,
        "resolved_feature_manifest_sha256": manifest_sha,
        "feature_null_audit": null_audit,
        "outer_split_feasibility": split_audit,
        "strict_historical_pit": False,
        "current_cohort_not_survivorship_free": True,
        "claim_boundary": cfg["claim_boundary"],
        "missing": missing,
    }
    _write(output_dir / "preregistration.json", payload)
    return payload


def _load_frozen_manifest(output_dir: Path) -> dict[str, Any]:
    path = output_dir / "resolved_feature_manifest.json"
    if not path.exists():
        raise FileNotFoundError(
            "run --stage plan and freeze resolved_feature_manifest.json before benchmark"
        )
    return _load_json(path)


def benchmark(
    config_path: Path,
    core_db: Path,
    output_dir: Path,
    horizons: list[int],
) -> dict[str, Any]:
    cfg = load_config(config_path)
    preregistration_path = output_dir / "preregistration.json"
    if not preregistration_path.exists():
        raise FileNotFoundError("run --stage plan before benchmark")
    preregistration = _load_json(preregistration_path)
    if preregistration.get("status") != "PASS":
        raise RuntimeError("V008.1 preregistration did not pass")
    manifest = _load_frozen_manifest(output_dir)
    current = resolve_feature_manifest(core_db, cfg)
    validate_frozen_manifest(current, cfg)
    if _sha(current) != _sha(manifest):
        raise RuntimeError("Core V003 feature schema changed after V008.1 plan")

    payload = {"benchmark_version": cfg["version"], "horizons": {}}
    for horizon in horizons:
        report, _, tables = run_horizon(core_db, int(horizon), cfg, manifest)
        _write(output_dir / f"h{int(horizon)}_benchmark.json", report)
        for name, table in tables.items():
            table.to_csv(
                output_dir / f"h{int(horizon)}_{name}.csv",
                index=False,
            )
        payload["horizons"][str(horizon)] = {
            "horizon_gate": report["horizon_gate"],
            "primary_comparison": report["primary_comparison"],
            "capacity_placebo": report["capacity_placebo"],
            "concentration_diagnostics": report["concentration_diagnostics"],
        }
    return payload


def summary(config_path: Path, output_dir: Path) -> dict[str, Any]:
    cfg = load_config(config_path)
    reports = {}
    for horizon in cfg["horizons_sessions"]:
        path = output_dir / f"h{int(horizon)}_benchmark.json"
        if not path.exists():
            raise FileNotFoundError(f"missing H{horizon} benchmark")
        reports[str(horizon)] = _load_json(path)

    h1_gate = reports[str(cfg["primary_horizon_sessions"])]["horizon_gate"]
    h1_status = str(h1_gate["status"])
    if h1_status == "PASS_DEVELOPMENTAL_REQUIRES_FRESH_HOLDOUT":
        interpretation = "H1_OWN_STATE_DEVELOPMENTAL_SIGNAL_REQUIRES_FRESH_HOLDOUT"
        next_action = "freeze this specification and evaluate it on a genuinely untouched future temporal block; do not add information sources or promote a model yet"
    elif "CLOSE_ENDOGENOUS_BRANCH" in h1_status:
        interpretation = "CURRENT_ENDOGENOUS_PRICE_VOLUME_BRANCH_CLOSED"
        next_action = "open one causally versioned external information block; prioritize option-implied distribution for scale/tails or expectations/revisions for location, in a separate decision"
    else:
        interpretation = "INCONCLUSIVE_NO_PROMOTION"
        next_action = "do not tune V008.1; review data/model diagnostics and make an explicit decision before any new experiment"

    payload = {
        "benchmark_version": cfg["version"],
        "status": "COMPLETE",
        "overall_interpretation": interpretation,
        "primary_horizon": int(cfg["primary_horizon_sessions"]),
        "primary_horizon_gate": h1_gate,
        "secondary_horizons_are_diagnostic_only": True,
        "horizon_matrix": {
            str(horizon): {
                "gate": reports[str(horizon)]["horizon_gate"],
                "primary_point_delta_pct": reports[str(horizon)][
                    "primary_comparison"
                ]["origin_day_equal_weight_delta_pct"],
                "concentration_diagnostics": reports[str(horizon)][
                    "concentration_diagnostics"
                ],
            }
            for horizon in cfg["horizons_sessions"]
        },
        "fresh_untouched_holdout_required_for_any_promotion": True,
        "no_confirmed_alpha": True,
        "next_action": next_action,
    }
    _write(output_dir / "benchmark_summary.json", payload)
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
    parser.add_argument("--v008-summary", type=Path, default=DEFAULT_V008_SUMMARY)
    parser.add_argument("--v008-h1", type=Path, default=DEFAULT_V008_H1)
    parser.add_argument("--v008-manifest", type=Path, default=DEFAULT_V008_MANIFEST)
    parser.add_argument("--output-dir", type=Path, default=DEFAULT_OUTPUT_DIR)
    parser.add_argument("--horizons", default="1,3,5,10")
    args = parser.parse_args()

    cfg = load_config(args.config)
    if args.stage == "plan":
        payload = plan(
            args.config,
            args.core_db,
            args.v008_summary,
            args.v008_h1,
            args.v008_manifest,
            args.output_dir,
        )
    elif args.stage == "benchmark":
        payload = benchmark(
            args.config,
            args.core_db,
            args.output_dir,
            _parse_horizons(
                args.horizons,
                list(cfg["horizons_sessions"]),
            ),
        )
    else:
        payload = summary(args.config, args.output_dir)
    print(json.dumps(payload, indent=2, sort_keys=True, allow_nan=False))


if __name__ == "__main__":
    main()

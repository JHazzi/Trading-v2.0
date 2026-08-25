from __future__ import annotations

import argparse
import gzip
import json
from pathlib import Path

from models.market.daily_v003_benchmark import (
    DEFAULT_CONFIG,
    DEFAULT_CORE_DB,
    load_config,
    load_horizon,
    run_horizon,
)
from evaluation.market.daily_v003_benchmark import (
    build_purged_day_folds,
    fold_summary,
)

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_REPORT_DIR = (
    ROOT / "reports" / "market_brain_daily_v003" / "benchmark_v001"
)


def write_json(path: Path, value: object) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(
        json.dumps(value, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )


def stage_plan(core_db: Path, cfg: dict) -> dict:
    result = {
        "status": "PASS",
        "benchmark_version": cfg["version"],
        "config": cfg,
        "horizons": {},
    }
    for h in cfg["horizons_sessions"]:
        frame = load_horizon(core_db, int(h))
        folds = build_purged_day_folds(
            frame,
            n_folds=int(cfg["outer_folds"]),
            initial_fraction=float(cfg["initial_fraction"]),
        )
        result["horizons"][str(h)] = {
            "rows": int(len(frame)),
            "assets": int(frame.asset_id.nunique()),
            "origin_days": int(frame.origin_trading_day.nunique()),
            "first_origin_day": str(frame.origin_trading_day.min()),
            "last_origin_day": str(frame.origin_trading_day.max()),
            "folds": fold_summary(folds),
        }
    return result


def stage_run(
    core_db: Path,
    cfg: dict,
    report_dir: Path,
    horizon: int,
) -> dict:
    result, oos = run_horizon(core_db, horizon, cfg)
    report_dir.mkdir(parents=True, exist_ok=True)
    write_json(report_dir / f"h{horizon}_benchmark.json", result)

    keep = [
        "state_id", "asset_id", "ticker", "sector",
        "origin_trading_day", "target_trading_day", "return_pct", "fold_id",
    ] + [c for c in oos.columns if c.startswith("pred_")]
    out_path = report_dir / f"h{horizon}_oos.csv.gz"
    oos[keep].to_csv(out_path, index=False, compression="gzip")
    return result


def stage_summary(cfg: dict, report_dir: Path) -> dict:
    horizon_reports = {}
    missing = []
    for h in cfg["horizons_sessions"]:
        path = report_dir / f"h{h}_benchmark.json"
        if not path.is_file():
            missing.append(int(h))
        else:
            horizon_reports[str(h)] = json.loads(
                path.read_text(encoding="utf-8")
            )
    if missing:
        raise FileNotFoundError(f"missing horizon reports: {missing}")

    summary = {
        "benchmark_version": cfg["version"],
        "interpretation_contract": {
            "primary_model": cfg["primary_model"],
            "primary_baseline": cfg["primary_baseline"],
            "positive_mae_delta_means_candidate_better": True,
            "do_not_choose_best_horizon_as_global_claim": True,
            "do_not_choose_best_model_as_primary_claim": True,
            "rf_is_deferred_robustness_not_missing_primary": True,
            "production_ready": False,
        },
        "horizons": horizon_reports,
    }
    write_json(report_dir / "benchmark_summary.json", summary)
    return summary


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--stage",
        required=True,
        choices=("plan", "run", "summary"),
    )
    p.add_argument("--core-db", type=Path, default=DEFAULT_CORE_DB)
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    p.add_argument("--horizon", type=int, choices=(1,3,5,10))
    args = p.parse_args()
    cfg = load_config(args.config)

    if args.stage == "plan":
        result = stage_plan(args.core_db, cfg)
        write_json(args.report_dir / "benchmark_plan.json", result)
    elif args.stage == "run":
        if args.horizon is None:
            raise SystemExit("--horizon is required for --stage run")
        result = stage_run(
            args.core_db, cfg, args.report_dir, args.horizon
        )
    else:
        result = stage_summary(cfg, args.report_dir)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

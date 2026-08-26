from __future__ import annotations

import argparse
import json
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = ROOT / "config" / "market_brain_daily_v0052_financial_conditions.json"


def write(path: Path, value: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(value, indent=2), encoding="utf-8")


def benchmark_plan(cfg: dict) -> dict:
    from models.market.daily_v0052_financial_conditions_benchmark import load_frames, load_v004_oos

    math_db = ROOT / cfg["v004_math_db"]
    state_db = ROOT / cfg["state_db"]
    v004_dir = ROOT / cfg["v004_report_dir"]
    out = ROOT / cfg["report_dir"]

    result = {"status": "PASS", "failures": [], "benchmark_version": cfg["version"], "horizons": {}}
    pure = sum(cfg["pure_external_features"].values(), [])
    full_external = [*pure, *cfg["interaction_features"]]

    for h in cfg["benchmark"]["horizons_sessions"]:
        market, sector, asset, sf, aaf, _ = load_frames(math_db, state_db, h, cfg)
        old = load_v004_oos(v004_dir / f"h{h}_factorized_oos.csv.gz")
        complete = int(market["financial_state_complete"].sum())
        missing = set(old["state_id"].astype(str)) - set(asset["state_id"].astype(str))
        r = {
            "market_rows": int(len(market)),
            "financial_state_complete_rows": complete,
            "financial_state_coverage_fraction": float(complete / len(market)),
            "sector_rows": int(len(sector)),
            "asset_rows": int(len(asset)),
            "stored_v004_oos_rows": int(len(old)),
            "v004_oos_states_missing_from_asset_frame": len(missing),
            "base_market_features": len(cfg["benchmark"]["base_market_features"]),
            "pure_external_features": len(pure),
            "interaction_features": len(cfg["interaction_features"]),
            "primary_external_features": len(full_external),
            "primary_market_features_total": len(cfg["benchmark"]["base_market_features"]) + len(full_external),
            "sector_features": len(sf),
            "asset_features": len(aaf),
            "same_day_vix_used": False,
            "SPY_QQQ_IWM_stacked": False,
        }
        if complete != len(market):
            result["failures"].append(f"h{h}_financial_state_incomplete")
        if missing:
            result["failures"].append(f"h{h}_v004_oos_state_missing")
        if r["base_market_features"] != 13:
            result["failures"].append(f"h{h}_base_feature_count")
        if r["primary_external_features"] != 20:
            result["failures"].append(f"h{h}_external_feature_count")
        if r["primary_market_features_total"] != 33:
            result["failures"].append(f"h{h}_total_feature_count")
        if len(aaf) != 20:
            result["failures"].append(f"h{h}_asset_feature_count")
        result["horizons"][str(h)] = r

    if result["failures"]:
        result["status"] = "FAIL"
    write(out / "benchmark_plan.json", result)
    return result


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--stage",
        required=True,
        choices=(
            "seed-assets", "acquire-etfs", "acquire-vix", "build", "audit",
            "benchmark-plan", "benchmark-run", "benchmark-summary",
        ),
    )
    p.add_argument("--horizon", type=int, choices=(1, 3, 5, 10))
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    a = p.parse_args()
    cfg = json.loads(a.config.read_text(encoding="utf-8"))

    if a.stage == "seed-assets":
        from tools.seed_financial_condition_reference_assets_v001 import seed
        result = seed(a.config)
    elif a.stage == "acquire-etfs":
        from ingestion.market_reference.financial_condition_etfs_v001 import acquire
        result = acquire(a.config)
    elif a.stage == "acquire-vix":
        from ingestion.market_reference.cboe_vix_daily_v001 import acquire
        result = acquire(a.config)
    elif a.stage == "build":
        from features.market.daily_v0052_financial_conditions import build
        result = build(a.config)
    elif a.stage == "audit":
        from evaluation.market.daily_v0052_financial_conditions_audit import audit
        result = audit(a.config)
    elif a.stage == "benchmark-plan":
        result = benchmark_plan(cfg)
    elif a.stage == "benchmark-run":
        if a.horizon is None:
            raise SystemExit("--horizon required")
        from models.market.daily_v0052_financial_conditions_benchmark import run_horizon
        math_db = ROOT / cfg["v004_math_db"]
        state_db = ROOT / cfg["state_db"]
        v004_dir = ROOT / cfg["v004_report_dir"]
        out = ROOT / cfg["report_dir"]
        result, oos = run_horizon(math_db, state_db, v004_dir, a.horizon, cfg)
        write(out / f"h{a.horizon}_financial_conditions_benchmark.json", result)
        keep = [
            "state_id", "asset_id", "ticker", "sector", "origin_trading_day",
            "target_trading_day", "return_pct", "fold_id", "pred_train_median",
            "pred_hgb_full", "pred_v004_additive_hgb", "pred_v004_replay",
            "pred_full_financial_conditions", "pred_vix_only", "pred_rates_only", "pred_credit_only",
        ]
        oos[keep].to_csv(
            out / f"h{a.horizon}_financial_conditions_oos.csv.gz",
            index=False, compression="gzip",
        )
    else:
        out = ROOT / cfg["report_dir"]
        horizons = {}
        for h in cfg["benchmark"]["horizons_sessions"]:
            path = out / f"h{h}_financial_conditions_benchmark.json"
            if not path.is_file():
                raise FileNotFoundError(path)
            horizons[str(h)] = json.loads(path.read_text(encoding="utf-8"))
        result = {
            "benchmark_version": cfg["version"],
            "interpretation_contract": {
                "primary": "full financial conditions vs V004",
                "secondary_ablations_are_diagnostic_only": True,
                "absolute_checkpoint": "V005.2 vs train median",
                "same_v004_oos_rows": True,
                "only_market_model_changed": True,
                "same_day_vix_used": False,
                "SPY_QQQ_IWM_stacked": False,
                "production_ready": False,
            },
            "horizons": horizons,
        }
        write(out / "financial_conditions_benchmark_summary.json", result)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

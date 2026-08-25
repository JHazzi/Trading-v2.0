from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np
import pandas as pd

from features.market.daily_v003_core import CROSS_FEATURES, SECTOR_FEATURES
from features.market.daily_v004_factorized_contract import CONTRACT

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CORE_DB = ROOT / "data" / "processed" / "market_daily_v003_core.db"
DEFAULT_BENCHMARK_DIR = (
    ROOT / "reports" / "market_brain_daily_v003" / "benchmark_v0011"
)
DEFAULT_REPORT = (
    ROOT / "reports" / "market_brain_daily_v004"
    / "factorization_foundation_postmortem.json"
)
HORIZONS = (1, 3, 5, 10)

MARKET_CONTEXT_FEATURES = [
    c for c in CROSS_FEATURES
    if not c.startswith("asset_minus_cross_section_")
]
ASSET_MARKET_RELATIVE_FEATURES = [
    c for c in CROSS_FEATURES
    if c.startswith("asset_minus_cross_section_")
]
SECTOR_CONTEXT_FEATURES = [
    c for c in SECTOR_FEATURES
    if not c.startswith("asset_minus_sector_")
]
ASSET_SECTOR_RELATIVE_FEATURES = [
    c for c in SECTOR_FEATURES
    if c.startswith("asset_minus_sector_")
]


def _load_benchmarks(report_dir: Path) -> dict[int, dict[str, Any]]:
    out = {}
    for h in HORIZONS:
        path = report_dir / f"h{h}_benchmark.json"
        if not path.is_file():
            raise FileNotFoundError(path)
        out[h] = json.loads(path.read_text(encoding="utf-8"))
    return out


def _benchmark_summary(items: dict[int, dict[str, Any]]) -> dict[str, Any]:
    horizons = {}
    primary_negative = True
    bootstrap_negative = True
    cross_harm = True

    for h, x in items.items():
        p = x["pooled"]
        c = x["comparisons"]
        primary = c["primary_train_median_vs_hgb_full"]
        own_cross = c["hgb_own_vs_own_cross"]
        cross_sector = c["hgb_own_cross_vs_full"]

        block = x["primary_moving_block_bootstrap"]
        block_ci = {
            k: {
                "point_delta_pct": float(v["point_delta_pct"]),
                "ci95": [float(v["ci95"][0]), float(v["ci95"][1])],
            }
            for k, v in block.items()
        }

        primary_negative &= float(
            primary["mae_delta_baseline_minus_candidate_pct"]
        ) < 0
        bootstrap_negative &= all(
            float(v["ci95"][1]) < 0 for v in block.values()
        )
        cross_harm &= float(
            own_cross["mae_delta_baseline_minus_candidate_pct"]
        ) < 0

        horizons[str(h)] = {
            "oos_rows": int(p["train_median"]["metrics"]["rows"]),
            "train_median_mae_pct": float(
                p["train_median"]["metrics"]["mae_pct"]
            ),
            "hgb_own_mae_pct": float(p["hgb_own"]["metrics"]["mae_pct"]),
            "hgb_own_cross_mae_pct": float(
                p["hgb_own_cross"]["metrics"]["mae_pct"]
            ),
            "hgb_full_mae_pct": float(p["hgb_full"]["metrics"]["mae_pct"]),
            "primary_delta_pct": float(
                primary["mae_delta_baseline_minus_candidate_pct"]
            ),
            "primary_candidate_win_rate": float(
                primary["candidate_abs_error_win_rate"]
            ),
            "own_to_cross_delta_pct": float(
                own_cross["mae_delta_baseline_minus_candidate_pct"]
            ),
            "cross_to_sector_delta_pct": float(
                cross_sector["mae_delta_baseline_minus_candidate_pct"]
            ),
            "hgb_own_spearman_ic": p["hgb_own"]["cross_section"][
                "daily_spearman_ic"
            ]["mean"],
            "hgb_full_spearman_ic": p["hgb_full"]["cross_section"][
                "daily_spearman_ic"
            ]["mean"],
            "sgd_huber_spearman_ic": p["sgd_huber_full"]["cross_section"][
                "daily_spearman_ic"
            ]["mean"],
            "primary_block_bootstrap": block_ci,
            "fold_primary_deltas_pct": [
                float(f["primary_delta"][
                    "mae_delta_baseline_minus_candidate_pct"
                ])
                for f in x["fold_results"]
            ],
        }

    return {
        "all_horizons_primary_negative": bool(primary_negative),
        "all_primary_block_ci_upper_below_zero": bool(bootstrap_negative),
        "all_horizons_own_to_cross_negative": bool(cross_harm),
        "horizons": horizons,
    }


def _target_decomposition(core_db: Path, horizon: int) -> dict[str, Any]:
    with sqlite3.connect(core_db) as conn:
        frame = pd.read_sql_query(
            """
            SELECT
                l.origin_trading_day,
                s.sector,
                l.return_pct
            FROM market_daily_v003_labels AS l
            JOIN market_daily_v003_states AS s
              ON s.state_id=l.state_id
            WHERE l.horizon_sessions=?
              AND l.label_status='usable'
            ORDER BY l.origin_trading_day
            """,
            conn,
            params=(int(horizon),),
        )

    if frame.empty:
        raise RuntimeError(f"no usable labels H{horizon}")

    y = pd.to_numeric(frame["return_pct"], errors="raise").to_numpy(float)
    frame["return_pct"] = y
    overall = float(np.mean(y))
    total_var = float(np.mean((y - overall) ** 2))

    day_mean = frame.groupby("origin_trading_day")["return_pct"].transform("mean")
    day_median = frame.groupby("origin_trading_day")["return_pct"].transform("median")
    sector_mean = frame.groupby(
        ["origin_trading_day", "sector"]
    )["return_pct"].transform("mean")
    sector_median = frame.groupby(
        ["origin_trading_day", "sector"]
    )["return_pct"].transform("median")

    between_market_var = float(
        np.mean((day_mean.to_numpy(float) - overall) ** 2)
    )
    within_market_var = float(
        np.mean((y - day_mean.to_numpy(float)) ** 2)
    )

    zero_mae = float(np.mean(np.abs(y)))
    oracle_market_mean_mae = float(
        np.mean(np.abs(y - day_mean.to_numpy(float)))
    )
    oracle_market_median_mae = float(
        np.mean(np.abs(y - day_median.to_numpy(float)))
    )
    oracle_sector_mean_mae = float(
        np.mean(np.abs(y - sector_mean.to_numpy(float)))
    )
    oracle_sector_median_mae = float(
        np.mean(np.abs(y - sector_median.to_numpy(float)))
    )

    return {
        "rows": int(len(frame)),
        "origin_days": int(frame["origin_trading_day"].nunique()),
        "sectors": int(frame["sector"].nunique()),
        "total_return_variance": total_var,
        "between_day_market_mean_variance": between_market_var,
        "within_day_residual_variance": within_market_var,
        "market_factor_variance_fraction": (
            None if total_var <= 0 else between_market_var / total_var
        ),
        "zero_mae_pct": zero_mae,
        "oracle_daily_mean_residual_mae_pct": oracle_market_mean_mae,
        "oracle_daily_median_residual_mae_pct": oracle_market_median_mae,
        "oracle_sector_mean_residual_mae_pct": oracle_sector_mean_mae,
        "oracle_sector_median_residual_mae_pct": oracle_sector_median_mae,
        "oracle_market_median_mae_reduction_fraction": (
            None if zero_mae <= 0
            else 1.0 - oracle_market_median_mae / zero_mae
        ),
        "oracle_sector_median_mae_reduction_fraction": (
            None if zero_mae <= 0
            else 1.0 - oracle_sector_median_mae / zero_mae
        ),
    }


def _sample_days(conn: sqlite3.Connection, n_days: int) -> list[str]:
    days = [
        str(r[0])
        for r in conn.execute(
            """
            SELECT DISTINCT trading_day
            FROM market_daily_v003_states
            ORDER BY trading_day
            """
        )
    ]
    if not days:
        raise RuntimeError("no core state days")
    n = min(n_days, len(days))
    positions = np.linspace(0, len(days)-1, num=n, dtype=int)
    return [days[int(i)] for i in positions]


def _feature_topology(core_db: Path, n_days: int) -> dict[str, Any]:
    with sqlite3.connect(core_db) as conn:
        days = _sample_days(conn, n_days)
        placeholders = ",".join("?" for _ in days)
        columns = list(dict.fromkeys(
            MARKET_CONTEXT_FEATURES
            + ASSET_MARKET_RELATIVE_FEATURES
            + SECTOR_CONTEXT_FEATURES
            + ASSET_SECTOR_RELATIVE_FEATURES
        ))
        select = ", ".join(columns)
        frame = pd.read_sql_query(
            f"""
            SELECT trading_day, sector, {select}
            FROM market_daily_v003_states
            WHERE trading_day IN ({placeholders})
            ORDER BY trading_day, sector
            """,
            conn,
            params=days,
        )

    result: dict[str, Any] = {
        "sample_days": len(days),
        "sample_rows": int(len(frame)),
        "market_context_features": {},
        "asset_market_relative_features": {},
        "sector_context_features": {},
        "asset_sector_relative_features": {},
    }

    def day_level(col: str) -> dict[str, float | None]:
        values = pd.to_numeric(frame[col], errors="coerce")
        total_std = float(values.std(ddof=0))
        within = (
            frame.assign(_v=values)
            .groupby("trading_day")["_v"]
            .std(ddof=0)
            .dropna()
        )
        median_within = float(within.median()) if len(within) else 0.0
        return {
            "total_std": total_std,
            "median_within_day_std": median_within,
            "within_to_total_std_ratio": (
                None if total_std <= 0 else median_within / total_std
            ),
        }

    def sector_day_level(col: str) -> dict[str, float | None]:
        values = pd.to_numeric(frame[col], errors="coerce")
        total_std = float(values.std(ddof=0))
        within = (
            frame.assign(_v=values)
            .groupby(["trading_day", "sector"])["_v"]
            .std(ddof=0)
            .dropna()
        )
        median_within = float(within.median()) if len(within) else 0.0
        return {
            "total_std": total_std,
            "median_within_sector_day_std": median_within,
            "within_to_total_std_ratio": (
                None if total_std <= 0 else median_within / total_std
            ),
        }

    for col in MARKET_CONTEXT_FEATURES:
        result["market_context_features"][col] = day_level(col)

    for col in ASSET_MARKET_RELATIVE_FEATURES:
        result["asset_market_relative_features"][col] = day_level(col)

    for col in SECTOR_CONTEXT_FEATURES:
        result["sector_context_features"][col] = sector_day_level(col)

    for col in ASSET_SECTOR_RELATIVE_FEATURES:
        result["asset_sector_relative_features"][col] = sector_day_level(col)

    def median_ratio(items: dict[str, dict[str, float | None]]) -> float | None:
        ratios = [
            v["within_to_total_std_ratio"]
            for v in items.values()
            if v["within_to_total_std_ratio"] is not None
        ]
        return None if not ratios else float(np.median(ratios))

    result["summary"] = {
        "median_market_context_within_day_ratio": median_ratio(
            result["market_context_features"]
        ),
        "median_asset_market_relative_within_day_ratio": median_ratio(
            result["asset_market_relative_features"]
        ),
        "median_sector_context_within_sector_day_ratio": median_ratio(
            result["sector_context_features"]
        ),
        "median_asset_sector_relative_within_sector_day_ratio": median_ratio(
            result["asset_sector_relative_features"]
        ),
        "interpretation": (
            "Context features should have low within-unit ratios if they are "
            "effectively day/sector-day variables. Asset-relative features "
            "should retain substantial within-unit variation and belong in "
            "the asset-level model rather than the market/sector factor model."
        ),
    }
    return result


def run_postmortem(
    *,
    core_db: Path = DEFAULT_CORE_DB,
    benchmark_dir: Path = DEFAULT_BENCHMARK_DIR,
    feature_sample_days: int = 180,
) -> dict[str, Any]:
    benchmarks = _load_benchmarks(benchmark_dir)
    bench = _benchmark_summary(benchmarks)
    targets = {
        str(h): _target_decomposition(core_db, h) for h in HORIZONS
    }
    topology = _feature_topology(core_db, feature_sample_days)

    reasons = []
    if bench["all_horizons_primary_negative"]:
        reasons.append("primary_hgb_full_loses_all_horizons")
    if bench["all_primary_block_ci_upper_below_zero"]:
        reasons.append("primary_loss_survives_temporal_block_bootstrap")
    if bench["all_horizons_own_to_cross_negative"]:
        reasons.append("cross_context_harms_hgb_all_horizons")

    return {
        "status": "PASS",
        "source_benchmark": "market_brain_daily_v003_benchmark_v0011",
        "v003_primary_claim": "REJECTED",
        "benchmark": bench,
        "target_decomposition": targets,
        "feature_topology": topology,
        "decision": {
            "next_stage": "MARKET_DAILY_V004_FACTORIZATION",
            "reasons": reasons,
            "do_not_do_yet": [
                "distributional_market_brain",
                "event_brain_integration",
                "external_proxy_expansion",
                "hyperparameter_search_on_v003",
            ],
            "hypothesis_to_test_not_fact": (
                "Repeated day-level and sector-day context in pooled "
                "asset-day nonlinear training contributes to unstable "
                "absolute-return mappings. Factorization separates effective "
                "statistical units and target components."
            ),
        },
        "v004_contract": CONTRACT,
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--core-db", type=Path, default=DEFAULT_CORE_DB)
    p.add_argument("--benchmark-dir", type=Path, default=DEFAULT_BENCHMARK_DIR)
    p.add_argument("--report", type=Path, default=DEFAULT_REPORT)
    p.add_argument("--feature-sample-days", type=int, default=180)
    args = p.parse_args()

    result = run_postmortem(
        core_db=args.core_db,
        benchmark_dir=args.benchmark_dir,
        feature_sample_days=args.feature_sample_days,
    )
    args.report.parent.mkdir(parents=True, exist_ok=True)
    args.report.write_text(
        json.dumps(result, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
from pathlib import Path

import numpy as np
import pandas as pd

from evaluation.backtest.global_time_split import global_time_split
from models.market.dataset import FEATURES as FEATURES_V001, TARGET, load_supervised_dataset
from models.market.dataset_v002 import FEATURES_V002, load_v002


def metrics(y_true: np.ndarray, pred: np.ndarray) -> dict[str, float | int]:
    err = pred - y_true
    abs_err = np.abs(err)

    return {
        "n": int(len(y_true)),
        "mae_pct": float(np.mean(abs_err)),
        "rmse_pct": float(np.sqrt(np.mean(err ** 2))),
        "directional_accuracy": float(
            np.mean(np.sign(y_true) == np.sign(pred))
        ),
        "median_abs_error_pct": float(np.median(abs_err)),
    }


def paired_metrics(
    y: np.ndarray,
    p1: np.ndarray,
    p2: np.ndarray,
) -> dict[str, float | int]:
    e1 = np.abs(p1 - y)
    e2 = np.abs(p2 - y)
    delta = e1 - e2  # positive => V002 is better

    return {
        "n": int(len(y)),
        "v002_win_rate": float(np.mean(e2 < e1)),
        "v001_win_rate": float(np.mean(e1 < e2)),
        "tie_rate": float(np.mean(e1 == e2)),
        "mean_abs_error_delta_v001_minus_v002_pct": float(np.mean(delta)),
        "median_abs_error_delta_v001_minus_v002_pct": float(np.median(delta)),
        "p10_delta_pct": float(np.quantile(delta, 0.10)),
        "p25_delta_pct": float(np.quantile(delta, 0.25)),
        "p75_delta_pct": float(np.quantile(delta, 0.75)),
        "p90_delta_pct": float(np.quantile(delta, 0.90)),
        "mean_direction_delta_v002_minus_v001_pct_points": float(
            100.0
            * (
                np.mean(np.sign(y) == np.sign(p2))
                - np.mean(np.sign(y) == np.sign(p1))
            )
        ),
    }


def load_artifact(path: Path) -> tuple[dict, object]:
    with path.open("rb") as fh:
        payload = pickle.load(fh)

    if not isinstance(payload, dict):
        raise RuntimeError(f"{path}: artifact no es dict")

    if "model" not in payload:
        raise RuntimeError(f"{path}: falta la clave 'model'")

    return payload, payload["model"]


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Comparación emparejada V001 vs V002 sobre exactamente los mismos outcome_id."
    )
    ap.add_argument("--horizon", type=int, required=True)
    ap.add_argument(
        "--v001-artifact",
        required=True,
    )
    ap.add_argument(
        "--v002-artifact",
        required=True,
    )
    ap.add_argument(
        "--db",
        default="data/database/market_data_v2.db",
    )
    ap.add_argument(
        "--output",
        default=None,
    )
    args = ap.parse_args()

    db = Path(args.db)
    a1, m1 = load_artifact(Path(args.v001_artifact))
    a2, m2 = load_artifact(Path(args.v002_artifact))

    if int(a1["horizon_seconds"]) != args.horizon:
        raise RuntimeError("El artifact V001 no coincide con --horizon")
    if int(a2["horizon_seconds"]) != args.horizon:
        raise RuntimeError("El artifact V002 no coincide con --horizon")

    d1 = load_supervised_dataset(db, args.horizon)
    d2 = load_v002(db, args.horizon)

    if d1.empty or d2.empty:
        raise RuntimeError("Uno de los datasets está vacío")

    s1 = global_time_split(d1)
    s2 = global_time_split(d2)

    if s1.cutoff != s2.cutoff:
        raise RuntimeError(
            f"Cutoffs distintos: {s1.cutoff} vs {s2.cutoff}"
        )

    # Exact test rows from each model.
    test1 = s1.test[
        ["outcome_id", "asset_id", "origin_time", TARGET]
    ].copy()
    test2 = s2.test[
        ["outcome_id", "asset_id", "origin_time", TARGET]
    ].copy()

    # Defensive uniqueness checks.
    if test1["outcome_id"].duplicated().any():
        raise RuntimeError("V001 test tiene outcome_id duplicados")
    if test2["outcome_id"].duplicated().any():
        raise RuntimeError("V002 test tiene outcome_id duplicados")

    pred1 = m1.predict(s1.test[FEATURES_V001])
    pred2 = m2.predict(s2.test[FEATURES_V002])

    pred_df1 = pd.DataFrame(
        {
            "outcome_id": s1.test["outcome_id"].to_numpy(),
            "asset_id": s1.test["asset_id"].to_numpy(),
            "origin_time": s1.test["origin_time"].to_numpy(),
            "y": s1.test[TARGET].to_numpy(),
            "pred_v001": np.asarray(pred1),
        }
    )

    pred_df2 = pd.DataFrame(
        {
            "outcome_id": s2.test["outcome_id"].to_numpy(),
            "asset_id": s2.test["asset_id"].to_numpy(),
            "origin_time": s2.test["origin_time"].to_numpy(),
            "y2": s2.test[TARGET].to_numpy(),
            "pred_v002": np.asarray(pred2),
        }
    )

    paired = pred_df1.merge(
        pred_df2,
        on=["outcome_id", "asset_id", "origin_time"],
        how="inner",
        validate="one_to_one",
    )

    if paired.empty:
        raise RuntimeError(
            "No quedaron observaciones emparejadas. "
            "No continúes hasta investigar la identidad de outcome_id."
        )

    if not np.allclose(
        paired["y"].to_numpy(),
        paired["y2"].to_numpy(),
        equal_nan=False,
    ):
        raise RuntimeError(
            "El target return_pct difiere entre V001 y V002 en las mismas observaciones."
        )

    y = paired["y"].to_numpy(dtype=float)
    p1 = paired["pred_v001"].to_numpy(dtype=float)
    p2 = paired["pred_v002"].to_numpy(dtype=float)

    result = {
        "horizon_seconds": args.horizon,
        "cutoff": s1.cutoff.isoformat(),
        "dataset_rows": {
            "v001_test": int(len(s1.test)),
            "v002_test": int(len(s2.test)),
            "paired_test": int(len(paired)),
            "paired_fraction_of_v001_test": float(len(paired) / len(s1.test)),
            "paired_fraction_of_v002_test": float(len(paired) / len(s2.test)),
        },
        "metrics_on_exact_same_observations": {
            "v001": metrics(y, p1),
            "v002": metrics(y, p2),
        },
        "paired_error_analysis": paired_metrics(y, p1, p2),
    }

    # Per-asset diagnostic; only assets with >= 100 paired observations.
    paired["abs_err_v001"] = np.abs(paired["pred_v001"] - paired["y"])
    paired["abs_err_v002"] = np.abs(paired["pred_v002"] - paired["y"])
    grouped = []

    for asset_id, g in paired.groupby("asset_id", sort=False):
        if len(g) < 100:
            continue
        mae1 = float(g["abs_err_v001"].mean())
        mae2 = float(g["abs_err_v002"].mean())
        grouped.append(
            {
                "asset_id": int(asset_id),
                "n": int(len(g)),
                "v001_mae_pct": mae1,
                "v002_mae_pct": mae2,
                "v002_improvement_pct": (
                    float(1.0 - mae2 / mae1) if mae1 != 0 else 0.0
                ),
            }
        )

    by_asset = pd.DataFrame(grouped)
    if not by_asset.empty:
        by_asset = by_asset.sort_values(
            "v002_improvement_pct", ascending=False
        )
        result["asset_diagnostics"] = {
            "assets_with_at_least_100_pairs": int(len(by_asset)),
            "median_asset_improvement_pct": float(
                by_asset["v002_improvement_pct"].median()
            ),
            "assets_v002_better_pct": float(
                np.mean(by_asset["v002_improvement_pct"] > 0.0) * 100.0
            ),
            "top_10_v002_wins": by_asset.head(10).to_dict(orient="records"),
            "top_10_v002_losses": by_asset.tail(10)
            .sort_values("v002_improvement_pct")
            .to_dict(orient="records"),
        }

    print(json.dumps(result, indent=2, ensure_ascii=False))

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(result, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        print(f"\nReporte guardado en: {output}")


if __name__ == "__main__":
    main()

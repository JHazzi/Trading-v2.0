from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.metrics import mean_absolute_error, root_mean_squared_error

from models.market.dataset import FEATURES as FEATURES_V001, TARGET, load_supervised_dataset
from models.market.dataset_v002 import FEATURES_V002, load_v002


KEYS = ["outcome_id", "asset_id", "origin_time"]


def load_paired_test(db, horizon, artifact_v001, artifact_v002):
    v1 = load_supervised_dataset(Path(db), horizon)
    v2 = load_v002(Path(db), horizon)

    # Separar test temporalmente de forma independiente.
    v1_times = pd.to_datetime(v1["origin_time"], utc=True)
    cutoff = v1_times.quantile(0.80)

    t1 = v1[v1_times >= cutoff].copy()

    v2_times = pd.to_datetime(v2["origin_time"], utc=True)
    t2 = v2[v2_times >= cutoff].copy()

    # Emparejamos exactamente las mismas observaciones.
    a = t1[KEYS + [TARGET]].copy()
    b = t2[KEYS + [TARGET]].copy()

    paired = a.merge(
        b,
        on=KEYS,
        how="inner",
        suffixes=("_v001", "_v002"),
        validate="one_to_one",
    )

    if paired.empty:
        raise RuntimeError("No quedaron observaciones emparejadas.")

    if not np.allclose(
        paired[f"{TARGET}_v001"],
        paired[f"{TARGET}_v002"],
    ):
        raise RuntimeError("Los targets V001/V002 no coinciden.")

    with Path(artifact_v001).open("rb") as f:
        m1 = pickle.load(f)["model"]

    with Path(artifact_v002).open("rb") as f:
        m2 = pickle.load(f)["model"]

    # Necesitamos reconstruir las filas originales de features.
    t1 = t1.merge(paired[KEYS], on=KEYS, how="inner", validate="one_to_one")
    t2 = t2.merge(paired[KEYS], on=KEYS, how="inner", validate="one_to_one")

    t1 = t1.sort_values(KEYS).reset_index(drop=True)
    t2 = t2.sort_values(KEYS).reset_index(drop=True)

    paired = paired.sort_values(KEYS).reset_index(drop=True)

    p1 = m1.predict(t1[FEATURES_V001])
    p2 = m2.predict(t2[FEATURES_V002])

    y = paired[f"{TARGET}_v001"].to_numpy(float)

    paired["pred_v001"] = p1
    paired["pred_v002"] = p2
    paired["abs_err_v001"] = np.abs(y - p1)
    paired["abs_err_v002"] = np.abs(y - p2)
    paired["error_delta"] = (
        paired["abs_err_v001"] - paired["abs_err_v002"]
    )

    paired["correct_v001"] = (
        np.sign(y) == np.sign(p1)
    )
    paired["correct_v002"] = (
        np.sign(y) == np.sign(p2)
    )

    # Feature ex-ante del modelo V002 para definir régimen.
    regime = t2[
        ["asset_id", "origin_time", "realized_vol_60m_pct"]
    ]

    paired = paired.merge(
        regime,
        on=["asset_id", "origin_time"],
        how="left",
        validate="one_to_one",
    )

    return paired, cutoff


def bootstrap_blocks(df, block_minutes=60, iterations=2000, seed=42):
    rng = np.random.default_rng(seed)

    d = df.copy()
    d["time"] = pd.to_datetime(d["origin_time"], utc=True)
    d["block"] = d["time"].dt.floor(f"{block_minutes}min")

    blocks = [
        g for _, g in d.groupby("block", sort=True)
    ]

    if len(blocks) < 2:
        raise RuntimeError("No hay suficientes bloques.")

    improvements = np.empty(iterations)
    win_rates = np.empty(iterations)
    da_deltas = np.empty(iterations)

    for i in range(iterations):
        selected = rng.integers(
            0,
            len(blocks),
            size=len(blocks),
        )

        sample = pd.concat(
            [blocks[j] for j in selected],
            ignore_index=True,
        )

        improvements[i] = sample["error_delta"].mean()

        win_rates[i] = np.mean(
            sample["abs_err_v002"]
            < sample["abs_err_v001"]
        )

        da_deltas[i] = (
            sample["correct_v002"].mean()
            - sample["correct_v001"].mean()
        )

    def ci(x):
        return {
            "estimate": float(x.mean()),
            "ci95_low": float(np.quantile(x, 0.025)),
            "ci95_high": float(np.quantile(x, 0.975)),
        }

    return {
        "mean_error_delta_v001_minus_v002_pct": ci(improvements),
        "v002_win_rate": ci(win_rates),
        "directional_accuracy_delta": ci(da_deltas),
        "block_minutes": block_minutes,
        "blocks": len(blocks),
        "iterations": iterations,
    }


def regimes(df):
    d = df.dropna(
        subset=["realized_vol_60m_pct"]
    ).copy()

    d["regime"] = pd.qcut(
        d["realized_vol_60m_pct"],
        4,
        labels=["low", "mid_low", "mid_high", "high"],
        duplicates="drop",
    )

    result = []

    for regime, g in d.groupby(
        "regime",
        observed=True,
    ):
        m1 = g["abs_err_v001"].mean()
        m2 = g["abs_err_v002"].mean()

        result.append({
            "regime": str(regime),
            "n": int(len(g)),
            "mean_volatility": float(
                g["realized_vol_60m_pct"].mean()
            ),
            "v001_mae": float(m1),
            "v002_mae": float(m2),
            "improvement_pct": float(
                100 * (m1 - m2) / m1
            ),
            "v001_directional_accuracy": float(
                g["correct_v001"].mean()
            ),
            "v002_directional_accuracy": float(
                g["correct_v002"].mean()
            ),
        })

    return result


def main():
    parser = argparse.ArgumentParser()

    parser.add_argument("--horizon", type=int, required=True)
    parser.add_argument("--v001-artifact", required=True)
    parser.add_argument("--v002-artifact", required=True)
    parser.add_argument(
        "--db",
        default="data/database/market_data_v2.db",
    )
    parser.add_argument("--block-minutes", type=int, default=60)
    parser.add_argument("--iterations", type=int, default=2000)
    parser.add_argument("--seed", type=int, default=42)
    parser.add_argument("--output")

    args = parser.parse_args()

    df, cutoff = load_paired_test(
        args.db,
        args.horizon,
        args.v001_artifact,
        args.v002_artifact,
    )

    y = df[f"{TARGET}_v001"].to_numpy(float)

    p1 = df["pred_v001"].to_numpy(float)
    p2 = df["pred_v002"].to_numpy(float)

    mae1 = mean_absolute_error(y, p1)
    mae2 = mean_absolute_error(y, p2)

    rmse1 = root_mean_squared_error(y, p1)
    rmse2 = root_mean_squared_error(y, p2)

    result = {
        "horizon_seconds": args.horizon,
        "cutoff": str(cutoff),
        "paired_rows": int(len(df)),
        "overall": {
            "v001_mae_pct": float(mae1),
            "v002_mae_pct": float(mae2),
            "mae_improvement_pct": float(
                100 * (mae1 - mae2) / mae1
            ),
            "v001_rmse_pct": float(rmse1),
            "v002_rmse_pct": float(rmse2),
            "rmse_improvement_pct": float(
                100 * (rmse1 - rmse2) / rmse1
            ),
            "v001_directional_accuracy": float(
                np.mean(np.sign(y) == np.sign(p1))
            ),
            "v002_directional_accuracy": float(
                np.mean(np.sign(y) == np.sign(p2))
            ),
        },
        "bootstrap": bootstrap_blocks(
            df,
            block_minutes=args.block_minutes,
            iterations=args.iterations,
            seed=args.seed,
        ),
        "volatility_regimes": regimes(df),
    }

    print(
        json.dumps(
            result,
            indent=2,
            ensure_ascii=False,
        )
    )

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(
            parents=True,
            exist_ok=True,
        )
        output.write_text(
            json.dumps(
                result,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )
        print(f"Reporte guardado en: {output}")


if __name__ == "__main__":
    main()

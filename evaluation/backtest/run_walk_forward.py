from __future__ import annotations

import argparse
from pathlib import Path

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import root_mean_squared_error
from sklearn.metrics import mean_absolute_error, mean_squared_error

try:
    from models.market.dataset import FEATURES, TARGET, load_supervised_dataset
    from models.market.train import walk_forward_split
except ImportError:
    from ...models.market.dataset import FEATURES, TARGET, load_supervised_dataset
    from ...models.market.train import walk_forward_split

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "database" / "market_data_v2.db"


def run(horizon_seconds: int, test_fraction: float = 0.2):
    df = load_supervised_dataset(DB_PATH, horizon_seconds)
    if df.empty:
        raise ValueError(
            f"No hay filas supervisadas para horizon={horizon_seconds}. "
            "Asegurate de tener realized_outcomes y feature_snapshots "
            "para los mismos timestamps."
        )

    train_df, test_df = walk_forward_split(df, test_fraction)
    model = RandomForestRegressor(
        n_estimators=200,
        min_samples_leaf=3,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(train_df[FEATURES], train_df[TARGET])
    pred = model.predict(test_df[FEATURES])
    actual = test_df[TARGET].to_numpy(dtype=float)
    zero_pred = np.zeros_like(actual)

    return {
        "horizon_seconds": horizon_seconds,
        "rows_total": len(df),
        "rows_train": len(train_df),
        "rows_test": len(test_df),
        "mae_pct": float(mean_absolute_error(actual, pred)),
        "rmse_pct": float(root_mean_squared_error(actual, pred)),
        "directional_accuracy": float(np.mean(np.sign(actual) == np.sign(pred))),
        "baseline_zero_mae_pct": float(mean_absolute_error(actual, zero_pred)),
        "mae_improvement_vs_zero_pct": float(
            mean_absolute_error(actual, zero_pred) - mean_absolute_error(actual, pred)
        ),
    }


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizon", type=int, default=300)
    args = parser.parse_args()
    print(run(args.horizon))


if __name__ == "__main__":
    main()

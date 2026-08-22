from __future__ import annotations

import argparse
import json
from pathlib import Path
import sys

import numpy as np
from sklearn.ensemble import RandomForestRegressor
from sklearn.impute import SimpleImputer
from sklearn.pipeline import make_pipeline

from models.market.dataset import FEATURES, TARGET, load_supervised_dataset
from evaluation.backtest.global_time_split import global_time_split
from evaluation.baselines.baselines import (
    asset_mean_prediction,
    global_mean_prediction,
    zero_prediction,
)
from evaluation.metrics.benchmark_metrics import evaluate_predictions


def make_model() -> object:
    return make_pipeline(
        SimpleImputer(strategy="median"),
        RandomForestRegressor(
            n_estimators=250,
            max_depth=12,
            min_samples_leaf=50,
            random_state=42,
            n_jobs=-1,
        ),
    )


def evaluate_horizon(db_path: Path, horizon: int, test_fraction: float) -> dict:
    df = load_supervised_dataset(db_path, horizon)

    split = global_time_split(df, test_fraction=test_fraction)
    train = split.train
    test = split.test

    actual = test[TARGET].to_numpy(dtype=float)

    predictions = {
        "zero": zero_prediction(train, test),
        "global_mean": global_mean_prediction(train, test, TARGET),
        "asset_mean": asset_mean_prediction(train, test, TARGET),
    }

    model = make_model()
    model.fit(train[FEATURES], train[TARGET])
    predictions["market_state"] = model.predict(test[FEATURES])

    results = {}
    for name, pred in predictions.items():
        results[name] = evaluate_predictions(actual, pred)

    results["meta"] = {
        "horizon_seconds": horizon,
        "rows_total": int(len(df)),
        "rows_train": int(len(train)),
        "rows_test": int(len(test)),
        "cutoff": split.cutoff.isoformat(),
        "train_min_time": train["origin_time"].min().isoformat(),
        "train_max_time": train["origin_time"].max().isoformat(),
        "test_min_time": test["origin_time"].min().isoformat(),
        "test_max_time": test["origin_time"].max().isoformat(),
        "feature_count": len(FEATURES),
    }
    return results


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--horizons",
        default="300,900,1800,3600",
        help="Horizontes en segundos, separados por coma.",
    )
    parser.add_argument("--test-fraction", type=float, default=0.20)
    parser.add_argument("--db", default="data/database/market_data_v2.db")
    parser.add_argument("--output", default="data/processed/market_benchmarks_v002.json")
    args = parser.parse_args()

    horizons = [int(x.strip()) for x in args.horizons.split(",") if x.strip()]
    db_path = Path(args.db)

    payload = {}
    for horizon in horizons:
        print(f"Evaluando horizon={horizon} ...", file=sys.stderr)
        payload[str(horizon)] = evaluate_horizon(
            db_path, horizon, args.test_fraction
        )

    output = Path(args.output)
    output.parent.mkdir(parents=True, exist_ok=True)
    output.write_text(
        json.dumps(payload, indent=2, ensure_ascii=False),
        encoding="utf-8",
    )

    print(json.dumps(payload, indent=2, ensure_ascii=False))
    print(f"\nReporte guardado en: {output}")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
import pickle
from pathlib import Path

from sklearn.ensemble import RandomForestRegressor

if __package__ in (None, ""):
    from pathlib import Path
    import sys

    sys.path.insert(
        0,
        str(Path(__file__).resolve().parents[2])
    )

try:
    from .dataset import FEATURES, TARGET, load_supervised_dataset
except ImportError:  # Direct invocation: python models/market/train.py
    from models.market.dataset import FEATURES, TARGET, load_supervised_dataset

ROOT = Path(__file__).resolve().parents[2]
DB_PATH = ROOT / "data" / "database" / "market_data_v2.db"
ARTIFACT_DIR = ROOT / "models" / "market" / "artifacts"


def walk_forward_split(df, test_fraction: float = 0.2):
    n = len(df)
    if n < 10:
        raise ValueError(f"Dataset demasiado pequeño para split temporal: {n} filas")
    test_n = max(1, int(round(n * test_fraction)))
    split = n - test_n
    if split < 2:
        raise ValueError(f"Dataset demasiado pequeño para split temporal: {n} filas")
    return df.iloc[:split].copy(), df.iloc[split:].copy()


def train(
    db_path: Path = DB_PATH,
    artifact_path: Path | None = None,
    horizon_seconds: int | None = 300,
):
    df = load_supervised_dataset(db_path, horizon_seconds)
    if len(df) < 20:
        raise ValueError(f"Muy pocos ejemplos supervisados: {len(df)}")

    train_df, test_df = walk_forward_split(df)
    model = RandomForestRegressor(
        n_estimators=200,
        min_samples_leaf=3,
        random_state=42,
        n_jobs=-1,
    )
    model.fit(train_df[FEATURES], train_df[TARGET])

    artifact_path = artifact_path or (ARTIFACT_DIR / "market_baseline_v001.pkl")
    artifact_path.parent.mkdir(parents=True, exist_ok=True)
    payload = {
        "model_version": "market_baseline_v001",
        "horizon_seconds": horizon_seconds,
        "feature_version": "market_state_v0.1.0",
        "features": FEATURES,
        "target": TARGET,
        "model": model,
        "rows_total": len(df),
        "rows_train": len(train_df),
        "rows_test": len(test_df),
        "min_coverage": 95.0,
    }
    with artifact_path.open("wb") as handle:
        pickle.dump(payload, handle)
    return payload


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizon", type=int, default=300)
    parser.add_argument("--artifact", type=Path, default=None)
    args = parser.parse_args()
    result = train(horizon_seconds=args.horizon, artifact_path=args.artifact)
    print({
        "artifact": str(args.artifact or ARTIFACT_DIR / "market_baseline_v001.pkl"),
        "rows_total": result["rows_total"],
        "rows_train": result["rows_train"],
        "rows_test": result["rows_test"],
        "horizon_seconds": result["horizon_seconds"],
    })


if __name__ == "__main__":
    main()

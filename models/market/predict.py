from __future__ import annotations

import argparse
import json
from pathlib import Path

import joblib
import pandas as pd

try:
    from .baseline import EnsembleQuantileBaseline
except ImportError:
    from baseline import EnsembleQuantileBaseline

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_ARTIFACT = ROOT / "models" / "market" / "artifacts" / "market_baseline_v001.pkl"


def predict_from_features(artifact_path: Path, feature_row: dict) -> dict:
    payload = joblib.load(artifact_path)
    model = payload["model"]
    columns = payload["feature_columns"]
    X = pd.DataFrame([{name: feature_row[name] for name in columns}])
    baseline = EnsembleQuantileBaseline(model)
    pred = baseline.predict_distribution(X)
    return pred.__dict__ | {
        "model_feature_version": payload["feature_version"],
        "target_version": payload["target_version"],
        "horizon_seconds": payload["horizon_seconds"],
    }


if __name__ == "__main__":
    parser = argparse.ArgumentParser()
    parser.add_argument("--artifact", type=Path, default=DEFAULT_ARTIFACT)
    parser.add_argument("--features-json", required=True)
    args = parser.parse_args()
    features = json.loads(Path(args.features_json).read_text())
    print(json.dumps(predict_from_features(args.artifact, features), indent=2))

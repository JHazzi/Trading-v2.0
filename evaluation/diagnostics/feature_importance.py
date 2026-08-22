from __future__ import annotations

import argparse
import json
import pickle
from pathlib import Path

import pandas as pd

from models.market.dataset import FEATURES, load_supervised_dataset
from evaluation.backtest.global_time_split import global_time_split


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--horizon", type=int, required=True)
    parser.add_argument(
        "--artifact",
        default="models/market/artifacts/market_baseline_v001.pkl",
    )
    parser.add_argument("--top", type=int, default=15)
    parser.add_argument(
        "--db",
        default="data/database/market_data_v2.db",
    )
    parser.add_argument("--output")
    args = parser.parse_args()

    artifact = Path(args.artifact)

    if not artifact.exists():
        raise SystemExit(f"No existe el artifact: {artifact}")

    with artifact.open("rb") as fh:
        artifact_data = pickle.load(fh)

    if not isinstance(artifact_data, dict):
        raise SystemExit(
            f"Se esperaba un dict en el artifact, se obtuvo: "
            f"{type(artifact_data)}"
        )

    if "model" not in artifact_data:
        raise SystemExit("El artifact no contiene la clave 'model'.")

    model = artifact_data["model"]

    if not hasattr(model, "feature_importances_"):
        raise SystemExit(
            f"El modelo guardado ({type(model)}) "
            "no expone feature_importances_."
        )

    importances = model.feature_importances_
    artifact_features = artifact_data.get("features", FEATURES)

    if len(importances) != len(artifact_features):
        raise SystemExit(
            f"Cantidad de importancias ({len(importances)}) "
            f"!= cantidad de features ({len(artifact_features)})."
        )

    df = load_supervised_dataset(
        Path(args.db),
        args.horizon,
    )

    split = global_time_split(df)

    importance = (
        pd.DataFrame(
            {
                "feature": artifact_features,
                "importance": importances,
            }
        )
        .sort_values("importance", ascending=False)
        .reset_index(drop=True)
    )

    payload = {
        "model_version": artifact_data.get("model_version"),
        "horizon_seconds": artifact_data.get("horizon_seconds"),
        "feature_version": artifact_data.get("feature_version"),
        "target": artifact_data.get("target"),
        "artifact_rows_total": artifact_data.get("rows_total"),
        "artifact_rows_train": artifact_data.get("rows_train"),
        "artifact_rows_test": artifact_data.get("rows_test"),
        "evaluation_rows_total": len(df),
        "evaluation_train_rows": len(split.train),
        "evaluation_test_rows": len(split.test),
        "cutoff": split.cutoff.isoformat(),
        "top_features": importance.head(args.top).to_dict(
            orient="records"
        ),
    }

    print(
        json.dumps(
            payload,
            indent=2,
            ensure_ascii=False,
        )
    )

    if args.output:
        output = Path(args.output)
        output.parent.mkdir(parents=True, exist_ok=True)
        output.write_text(
            json.dumps(
                payload,
                indent=2,
                ensure_ascii=False,
            ),
            encoding="utf-8",
        )

        print(f"\nReporte guardado en: {output}")


if __name__ == "__main__":
    main()
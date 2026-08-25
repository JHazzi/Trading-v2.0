from __future__ import annotations

import argparse
import json
import pickle
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.ensemble import RandomForestRegressor
from sklearn.metrics import mean_absolute_error, mean_squared_error
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder

from models.events.dataset_v001 import (
    EVENT_FEATURES_CATEGORICAL,
    EVENT_FEATURES_NUMERIC,
    MARKET_FEATURES,
    MARKET_FEATURE_VERSION,
    TARGET,
    load_dataset,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
DEFAULT_ARTIFACTS = ROOT / "models" / "events" / "artifacts"

MODEL_VERSION = "event_brain_v001_residual"
EVENT_FEATURE_VERSION = "event_state_v001"
LABEL_VERSION = "event_reaction_daily_v001"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rf(seed: int) -> RandomForestRegressor:
    return RandomForestRegressor(
        n_estimators=250,
        min_samples_leaf=5,
        max_features=0.8,
        random_state=seed,
        n_jobs=-1,
    )


def _market_pipeline(seed: int) -> Pipeline:
    return Pipeline([
        ("model", _rf(seed)),
    ])


def _event_pipeline(
    numeric: list[str],
    categorical: list[str],
    seed: int,
) -> Pipeline:
    prep = ColumnTransformer(
        [
            ("num", "passthrough", numeric),
            (
                "cat",
                OneHotEncoder(handle_unknown="ignore"),
                categorical,
            ),
        ],
        remainder="drop",
    )
    return Pipeline([
        ("prep", prep),
        ("model", _rf(seed)),
    ])


def _metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    return {
        "mae_pct": float(mean_absolute_error(y, p)),
        "rmse_pct": float(np.sqrt(mean_squared_error(y, p))),
        "directional_accuracy": float(
            np.mean(np.sign(y) == np.sign(p))
        ),
        "median_abs_error_pct": float(np.median(np.abs(y - p))),
    }


def _temporal_cutoff(df: pd.DataFrame, train_fraction: float) -> str:
    times = np.array(sorted(df["state_time"].astype(str).unique()))
    if len(times) < 10:
        raise ValueError("Muy pocos timestamps únicos para split temporal")
    idx = min(len(times) - 1, max(1, int(len(times) * train_fraction)))
    return str(times[idx])


def _oof_market_predictions(
    train: pd.DataFrame,
    *,
    seed: int,
    folds: int = 4,
) -> pd.Series:
    unique_times = np.array(sorted(train["state_time"].astype(str).unique()))
    if len(unique_times) < 12:
        raise ValueError(
            "Muy pocos timestamps para OOF temporal del residual Event Brain"
        )

    initial = max(3, int(len(unique_times) * 0.40))
    remaining = unique_times[initial:]
    chunks = [x for x in np.array_split(remaining, folds) if len(x)]
    out = pd.Series(index=train.index, dtype=float)

    for fold_idx, chunk in enumerate(chunks):
        start_time = str(chunk[0])
        end_time = str(chunk[-1])
        fit = train[train["state_time"].astype(str) < start_time]
        val = train[
            (train["state_time"].astype(str) >= start_time)
            & (train["state_time"].astype(str) <= end_time)
        ]
        if len(fit) < 20 or val.empty:
            continue
        model = _market_pipeline(seed + fold_idx)
        model.fit(fit[MARKET_FEATURES], fit[TARGET])
        out.loc[val.index] = model.predict(val[MARKET_FEATURES])

    return out


def train(
    db: Path,
    horizon_sessions: int,
    *,
    artifact_dir: Path,
    min_rows: int = 200,
    train_fraction: float = 0.80,
    seed: int = 42,
) -> dict[str, object]:
    df = load_dataset(db, horizon_sessions)
    if len(df) < min_rows:
        raise RuntimeError(
            f"Dataset insuficiente para entrenamiento serio: {len(df)} filas "
            f"< min_rows={min_rows}. El pipeline está listo; amplíe eventos y "
            f"precios antes de bajar este gate."
        )

    cutoff = _temporal_cutoff(df, train_fraction)
    train_df = df[df["state_time"].astype(str) < cutoff].copy()
    test_df = df[df["state_time"].astype(str) >= cutoff].copy()
    if len(train_df) < 50 or len(test_df) < 20:
        raise RuntimeError(
            f"Split temporal insuficiente train={len(train_df)} test={len(test_df)}"
        )

    started = utc_now()
    training_run_id = str(uuid.uuid4())

    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO event_brain_training_runs(
                training_run_id, model_version, horizon_sessions,
                event_feature_version, market_feature_version, label_version,
                started_at, status, temporal_cutoff, configuration_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
            """,
            (
                training_run_id,
                MODEL_VERSION,
                horizon_sessions,
                EVENT_FEATURE_VERSION,
                MARKET_FEATURE_VERSION,
                LABEL_VERSION,
                started,
                cutoff,
                json.dumps(
                    {
                        "split": "chronological",
                        "train_fraction": train_fraction,
                        "residual_training": "expanding_market_oof",
                        "min_rows": min_rows,
                        "seed": seed,
                    },
                    sort_keys=True,
                ),
            ),
        )
        conn.commit()

    try:
        market_model = _market_pipeline(seed)
        event_only = _event_pipeline(
            EVENT_FEATURES_NUMERIC,
            EVENT_FEATURES_CATEGORICAL,
            seed + 100,
        )

        oof = _oof_market_predictions(train_df, seed=seed)
        residual_train = train_df.loc[oof.dropna().index].copy()
        residual_train["market_oof"] = oof.dropna()
        residual_train["residual_target"] = (
            residual_train[TARGET] - residual_train["market_oof"]
        )
        if len(residual_train) < max(30, min_rows // 4):
            raise RuntimeError(
                f"OOF residual dataset insuficiente: {len(residual_train)}"
            )

        fusion_numeric = MARKET_FEATURES + EVENT_FEATURES_NUMERIC
        residual_model = _event_pipeline(
            fusion_numeric,
            EVENT_FEATURES_CATEGORICAL,
            seed + 200,
        )

        market_model.fit(train_df[MARKET_FEATURES], train_df[TARGET])
        event_only.fit(
            train_df[
                EVENT_FEATURES_NUMERIC + EVENT_FEATURES_CATEGORICAL
            ],
            train_df[TARGET],
        )
        residual_model.fit(
            residual_train[
                fusion_numeric + EVENT_FEATURES_CATEGORICAL
            ],
            residual_train["residual_target"],
        )

        y = test_df[TARGET].to_numpy(float)
        market_pred = market_model.predict(test_df[MARKET_FEATURES])
        event_pred = event_only.predict(
            test_df[
                EVENT_FEATURES_NUMERIC + EVENT_FEATURES_CATEGORICAL
            ]
        )
        residual_pred = residual_model.predict(
            test_df[fusion_numeric + EVENT_FEATURES_CATEGORICAL]
        )
        fused_pred = market_pred + residual_pred
        zero_pred = np.zeros_like(y)

        metrics = {
            "zero": _metrics(y, zero_pred),
            "market_only": _metrics(y, market_pred),
            "event_only": _metrics(y, event_pred),
            "market_plus_event": _metrics(y, fused_pred),
        }
        metrics["incremental"] = {
            "mae_delta_market_minus_fused_pct": float(
                metrics["market_only"]["mae_pct"]
                - metrics["market_plus_event"]["mae_pct"]
            ),
            "mae_improvement_vs_market_pct": float(
                100.0
                * (
                    metrics["market_only"]["mae_pct"]
                    - metrics["market_plus_event"]["mae_pct"]
                )
                / metrics["market_only"]["mae_pct"]
            ),
            "directional_accuracy_delta_pct_points": float(
                100.0
                * (
                    metrics["market_plus_event"]["directional_accuracy"]
                    - metrics["market_only"]["directional_accuracy"]
                )
            ),
        }

        artifact_dir.mkdir(parents=True, exist_ok=True)
        artifact = artifact_dir / (
            f"event_brain_v001_{horizon_sessions}sessions.pkl"
        )
        with artifact.open("wb") as fh:
            pickle.dump(
                {
                    "model_version": MODEL_VERSION,
                    "horizon_sessions": horizon_sessions,
                    "event_feature_version": EVENT_FEATURE_VERSION,
                    "market_feature_version": MARKET_FEATURE_VERSION,
                    "label_version": LABEL_VERSION,
                    "market_features": MARKET_FEATURES,
                    "event_features_numeric": EVENT_FEATURES_NUMERIC,
                    "event_features_categorical": EVENT_FEATURES_CATEGORICAL,
                    "market_model": market_model,
                    "event_only_model": event_only,
                    "residual_model": residual_model,
                    "temporal_cutoff": cutoff,
                    "metrics": metrics,
                },
                fh,
            )

        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                UPDATE event_brain_training_runs
                SET finished_at=?, status='completed',
                    train_rows=?, test_rows=?, oof_rows=?,
                    artifact_path=?, metrics_json=?
                WHERE training_run_id=?
                """,
                (
                    utc_now(),
                    len(train_df),
                    len(test_df),
                    len(residual_train),
                    str(artifact),
                    json.dumps(metrics, sort_keys=True),
                    training_run_id,
                ),
            )
            conn.commit()

        return {
            "training_run_id": training_run_id,
            "model_version": MODEL_VERSION,
            "horizon_sessions": horizon_sessions,
            "rows": len(df),
            "train_rows": len(train_df),
            "test_rows": len(test_df),
            "oof_rows": len(residual_train),
            "temporal_cutoff": cutoff,
            "artifact": str(artifact),
            "metrics": metrics,
        }

    except Exception as error:
        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                UPDATE event_brain_training_runs
                SET finished_at=?, status='failed', error_json=?
                WHERE training_run_id=?
                """,
                (
                    utc_now(),
                    json.dumps(
                        {
                            "type": type(error).__name__,
                            "message": str(error),
                        },
                        sort_keys=True,
                    ),
                    training_run_id,
                ),
            )
            conn.commit()
        raise


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Event Brain v0.1 incremental residual benchmark"
    )
    ap.add_argument("--horizon-sessions", type=int, required=True)
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    ap.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACTS)
    ap.add_argument("--min-rows", type=int, default=200)
    ap.add_argument("--seed", type=int, default=42)
    args = ap.parse_args()

    result = train(
        args.db,
        args.horizon_sessions,
        artifact_dir=args.artifact_dir,
        min_rows=args.min_rows,
        seed=args.seed,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

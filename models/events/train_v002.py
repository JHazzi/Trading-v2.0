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

from evaluation.events.audit_v002 import (
    DatasetGates,
    audit_horizon,
)
from evaluation.events.walkforward_v002 import (
    build_purged_event_folds,
    expanding_oof_market_predictions,
)
from models.events.dataset_v002 import (
    EVENT_FEATURES_CATEGORICAL,
    EVENT_FEATURES_NUMERIC,
    EVENT_FEATURE_VERSION,
    LABEL_VERSION,
    MARKET_CATEGORICAL,
    MARKET_FEATURES,
    MARKET_FEATURE_VERSION,
    TARGET,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
DEFAULT_ARTIFACT_DIR = ROOT / "models" / "events" / "artifacts"
DEFAULT_REPORT_DIR = ROOT / "reports" / "event_brain_v002"

MODEL_VERSION = "event_brain_v002_purged_residual_capacity_control"


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def _rf(seed: int) -> RandomForestRegressor:
    return RandomForestRegressor(
        n_estimators=400,
        min_samples_leaf=6,
        max_features=0.8,
        random_state=seed,
        n_jobs=-1,
    )


def _pipeline(
    numeric: list[str],
    categorical: list[str],
    seed: int,
) -> Pipeline:
    transformers = [("num", "passthrough", numeric)]
    if categorical:
        transformers.append(
            (
                "cat",
                OneHotEncoder(handle_unknown="ignore"),
                categorical,
            )
        )
    prep = ColumnTransformer(
        transformers,
        remainder="drop",
    )
    return Pipeline(
        [
            ("prep", prep),
            ("model", _rf(seed)),
        ]
    )


def _metrics(y: np.ndarray, p: np.ndarray) -> dict[str, float]:
    return {
        "mae_pct": float(mean_absolute_error(y, p)),
        "rmse_pct": float(np.sqrt(mean_squared_error(y, p))),
        "directional_accuracy": float(
            np.mean(np.sign(y) == np.sign(p))
        ),
        "median_abs_error_pct": float(np.median(np.abs(y - p))),
    }


def _paired_comparison(
    frame: pd.DataFrame,
    *,
    baseline_col: str,
    candidate_col: str,
    bootstrap_reps: int,
    seed: int,
) -> dict[str, object]:
    y = frame[TARGET].to_numpy(float)
    base = frame[baseline_col].to_numpy(float)
    cand = frame[candidate_col].to_numpy(float)

    base_abs = np.abs(y - base)
    cand_abs = np.abs(y - cand)
    point_mae_delta = float(np.mean(base_abs - cand_abs))
    point_direction_delta = float(
        np.mean(np.sign(y) == np.sign(cand))
        - np.mean(np.sign(y) == np.sign(base))
    )
    win_rate = float(np.mean(cand_abs < base_abs))

    grouped = {
        str(day): np.asarray(index, dtype=int)
        for day, index in frame.groupby(
            "origin_trading_day"
        ).indices.items()
    }
    days = np.array(sorted(grouped), dtype=object)
    rng = np.random.default_rng(seed)
    mae_deltas: list[float] = []
    dir_deltas: list[float] = []
    win_rates: list[float] = []

    for _ in range(bootstrap_reps):
        sampled_days = rng.choice(days, size=len(days), replace=True)
        sampled_index = np.concatenate(
            [grouped[str(day)] for day in sampled_days]
        )
        sy = y[sampled_index]
        sb = base[sampled_index]
        sc = cand[sampled_index]
        sb_abs = np.abs(sy - sb)
        sc_abs = np.abs(sy - sc)
        mae_deltas.append(float(np.mean(sb_abs - sc_abs)))
        dir_deltas.append(
            float(
                np.mean(np.sign(sy) == np.sign(sc))
                - np.mean(np.sign(sy) == np.sign(sb))
            )
        )
        win_rates.append(float(np.mean(sc_abs < sb_abs)))

    def ci(values: list[float]) -> list[float]:
        return [
            float(np.quantile(values, 0.025)),
            float(np.quantile(values, 0.975)),
        ]

    return {
        "baseline": baseline_col,
        "candidate": candidate_col,
        "mae_delta_baseline_minus_candidate_pct": point_mae_delta,
        "mae_delta_ci95": ci(mae_deltas),
        "directional_accuracy_delta": point_direction_delta,
        "directional_accuracy_delta_ci95": ci(dir_deltas),
        "candidate_abs_error_win_rate": win_rate,
        "candidate_abs_error_win_rate_ci95": ci(win_rates),
        "bootstrap_unit": "origin_trading_day",
        "bootstrap_reps": bootstrap_reps,
    }


def _fit_market_predict(
    fit_frame: pd.DataFrame,
    validation_frame: pd.DataFrame,
    fold_seed: int,
) -> np.ndarray:
    model = _pipeline(
        MARKET_FEATURES,
        MARKET_CATEGORICAL,
        fold_seed,
    )
    model.fit(
        fit_frame[MARKET_FEATURES + MARKET_CATEGORICAL],
        fit_frame[TARGET],
    )
    return model.predict(
        validation_frame[MARKET_FEATURES + MARKET_CATEGORICAL]
    )


def _residual_training_frame(
    train_df: pd.DataFrame,
    *,
    seed: int,
) -> pd.DataFrame:
    oof = expanding_oof_market_predictions(
        train_df,
        fit_predict=lambda fit, val, fold_id: _fit_market_predict(
            fit,
            val,
            seed + 1000 + fold_id,
        ),
        n_folds=3,
        initial_fraction=0.40,
        min_train_rows=35,
        min_test_rows=8,
    )
    usable = oof.dropna()
    residual = train_df.loc[usable.index].copy()
    residual["market_oof_prediction"] = usable
    residual["residual_target"] = (
        residual[TARGET] - residual["market_oof_prediction"]
    )
    return residual


def _fold_predictions(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    *,
    seed: int,
) -> dict[str, np.ndarray]:
    market_model = _pipeline(
        MARKET_FEATURES,
        MARKET_CATEGORICAL,
        seed,
    )
    event_only_model = _pipeline(
        EVENT_FEATURES_NUMERIC,
        EVENT_FEATURES_CATEGORICAL,
        seed + 100,
    )

    market_model.fit(
        train_df[MARKET_FEATURES + MARKET_CATEGORICAL],
        train_df[TARGET],
    )
    event_only_model.fit(
        train_df[
            EVENT_FEATURES_NUMERIC + EVENT_FEATURES_CATEGORICAL
        ],
        train_df[TARGET],
    )

    residual_train = _residual_training_frame(
        train_df,
        seed=seed + 200,
    )
    if len(residual_train) < 45:
        raise RuntimeError(
            f"Residual OOF insuficiente: {len(residual_train)}"
        )

    event_residual_model = _pipeline(
        EVENT_FEATURES_NUMERIC,
        EVENT_FEATURES_CATEGORICAL,
        seed + 300,
    )
    capacity_control_model = _pipeline(
        MARKET_FEATURES,
        MARKET_CATEGORICAL,
        seed + 400,
    )
    contextual_event_model = _pipeline(
        MARKET_FEATURES + EVENT_FEATURES_NUMERIC,
        MARKET_CATEGORICAL + EVENT_FEATURES_CATEGORICAL,
        seed + 500,
    )

    event_residual_model.fit(
        residual_train[
            EVENT_FEATURES_NUMERIC + EVENT_FEATURES_CATEGORICAL
        ],
        residual_train["residual_target"],
    )
    capacity_control_model.fit(
        residual_train[
            MARKET_FEATURES + MARKET_CATEGORICAL
        ],
        residual_train["residual_target"],
    )
    contextual_event_model.fit(
        residual_train[
            MARKET_FEATURES
            + EVENT_FEATURES_NUMERIC
            + MARKET_CATEGORICAL
            + EVENT_FEATURES_CATEGORICAL
        ],
        residual_train["residual_target"],
    )

    market_pred = market_model.predict(
        test_df[MARKET_FEATURES + MARKET_CATEGORICAL]
    )
    event_only_pred = event_only_model.predict(
        test_df[
            EVENT_FEATURES_NUMERIC + EVENT_FEATURES_CATEGORICAL
        ]
    )
    event_residual_pred = event_residual_model.predict(
        test_df[
            EVENT_FEATURES_NUMERIC + EVENT_FEATURES_CATEGORICAL
        ]
    )
    control_residual_pred = capacity_control_model.predict(
        test_df[MARKET_FEATURES + MARKET_CATEGORICAL]
    )
    contextual_event_residual_pred = contextual_event_model.predict(
        test_df[
            MARKET_FEATURES
            + EVENT_FEATURES_NUMERIC
            + MARKET_CATEGORICAL
            + EVENT_FEATURES_CATEGORICAL
        ]
    )

    return {
        "pred_zero": np.zeros(len(test_df), dtype=float),
        "pred_market": market_pred,
        "pred_event_only": event_only_pred,
        "pred_market_plus_event": market_pred + event_residual_pred,
        "pred_capacity_control": market_pred + control_residual_pred,
        "pred_contextual_event": (
            market_pred + contextual_event_residual_pred
        ),
    }


def _subgroup_metrics(
    oos: pd.DataFrame,
    *,
    group_column: str,
    min_rows: int = 15,
) -> dict[str, object]:
    out: dict[str, object] = {}
    for value, group in oos.groupby(group_column):
        if len(group) < min_rows:
            continue
        y = group[TARGET].to_numpy(float)
        out[str(value)] = {
            "rows": len(group),
            "market": _metrics(y, group["pred_market"].to_numpy(float)),
            "capacity_control": _metrics(
                y,
                group["pred_capacity_control"].to_numpy(float),
            ),
            "contextual_event": _metrics(
                y,
                group["pred_contextual_event"].to_numpy(float),
            ),
        }
    return out


def train_and_evaluate(
    db: Path,
    horizon_sessions: int,
    *,
    artifact_dir: Path = DEFAULT_ARTIFACT_DIR,
    report_dir: Path = DEFAULT_REPORT_DIR,
    seed: int = 42,
    outer_folds: int = 4,
    bootstrap_reps: int = 2000,
    gates: DatasetGates = DatasetGates(),
) -> dict[str, object]:
    df, audit = audit_horizon(
        db,
        horizon_sessions,
        gates=gates,
    )
    if audit["status"] != "PASS":
        raise RuntimeError(
            "Dataset gate FAIL: "
            + json.dumps(audit, ensure_ascii=False)
        )

    folds = build_purged_event_folds(
        df,
        n_folds=outer_folds,
        initial_fraction=0.45,
        min_train_rows=80,
        min_test_rows=15,
    )

    started_at = utc_now()
    training_run_id = uuid.uuid4().hex

    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO event_brain_training_runs(
                training_run_id,
                model_version,
                horizon_sessions,
                event_feature_version,
                market_feature_version,
                label_version,
                started_at,
                status,
                temporal_cutoff,
                configuration_json
            ) VALUES (?, ?, ?, ?, ?, ?, ?, 'running', ?, ?)
            """,
            (
                training_run_id,
                MODEL_VERSION,
                horizon_sessions,
                EVENT_FEATURE_VERSION,
                MARKET_FEATURE_VERSION,
                LABEL_VERSION,
                started_at,
                "purged_multi_fold",
                json.dumps(
                    {
                        "evaluation": "purged_event_grouped_walk_forward",
                        "outer_folds_requested": outer_folds,
                        "outer_folds_built": len(folds),
                        "market_context":
                            "asset+leave_one_out_cross_section+sector",
                        "capacity_control":
                            "market+residual(market)",
                        "primary_candidate":
                            "market+residual(market,event)",
                        "bootstrap_unit": "origin_trading_day",
                        "bootstrap_reps": bootstrap_reps,
                        "seed": seed,
                        "dataset_gates": gates.__dict__,
                    },
                    sort_keys=True,
                ),
            ),
        )
        conn.commit()

    try:
        fold_frames: list[pd.DataFrame] = []
        fold_summaries: list[dict[str, object]] = []

        for fold in folds:
            train_df = df.loc[list(fold.train_index)].copy()
            test_df = df.loc[list(fold.test_index)].copy()
            predictions = _fold_predictions(
                train_df,
                test_df,
                seed=seed + fold.fold_id * 10000,
            )
            for name, values in predictions.items():
                test_df[name] = values
            test_df["fold_id"] = fold.fold_id
            fold_frames.append(test_df)

            y = test_df[TARGET].to_numpy(float)
            fold_summaries.append(
                {
                    "fold_id": fold.fold_id,
                    "first_test_anchor_day":
                        fold.first_test_anchor_day,
                    "last_test_anchor_day":
                        fold.last_test_anchor_day,
                    "train_rows": len(train_df),
                    "test_rows": len(test_df),
                    "market": _metrics(
                        y,
                        test_df["pred_market"].to_numpy(float),
                    ),
                    "capacity_control": _metrics(
                        y,
                        test_df[
                            "pred_capacity_control"
                        ].to_numpy(float),
                    ),
                    "contextual_event": _metrics(
                        y,
                        test_df[
                            "pred_contextual_event"
                        ].to_numpy(float),
                    ),
                }
            )

        oos = pd.concat(
            fold_frames,
            ignore_index=False,
        ).sort_values(
            ["origin_trading_day", "state_time", "reaction_label_id"]
        )

        y = oos[TARGET].to_numpy(float)
        model_columns = {
            "zero": "pred_zero",
            "market": "pred_market",
            "event_only": "pred_event_only",
            "market_plus_event": "pred_market_plus_event",
            "capacity_control": "pred_capacity_control",
            "contextual_event": "pred_contextual_event",
        }
        metrics = {
            name: _metrics(y, oos[column].to_numpy(float))
            for name, column in model_columns.items()
        }

        comparisons = {
            "simple_event_vs_market": _paired_comparison(
                oos,
                baseline_col="pred_market",
                candidate_col="pred_market_plus_event",
                bootstrap_reps=bootstrap_reps,
                seed=seed + 1,
            ),
            "capacity_control_vs_contextual_event":
                _paired_comparison(
                    oos,
                    baseline_col="pred_capacity_control",
                    candidate_col="pred_contextual_event",
                    bootstrap_reps=bootstrap_reps,
                    seed=seed + 2,
                ),
        }

        report = {
            "training_run_id": training_run_id,
            "model_version": MODEL_VERSION,
            "horizon_sessions": horizon_sessions,
            "dataset_audit": audit,
            "folds": fold_summaries,
            "pooled_oos_rows": len(oos),
            "metrics": metrics,
            "comparisons": comparisons,
            "per_asset": _subgroup_metrics(
                oos,
                group_column="asset_id",
                min_rows=15,
            ),
            "per_event_type": _subgroup_metrics(
                oos,
                group_column="event_type",
                min_rows=15,
            ),
            "interpretation_contract": {
                "primary_incremental_test":
                    "capacity_control_vs_contextual_event",
                "positive_mae_delta_means_event_features_help":
                    True,
                "production_ready": False,
                "distributional_output_implemented": False,
                "strict_pit_event_evidence": False,
            },
        }

        artifact_dir.mkdir(parents=True, exist_ok=True)
        report_dir.mkdir(parents=True, exist_ok=True)

        oos_path = report_dir / (
            f"event_brain_v002_h{horizon_sessions}_oos.csv"
        )
        report_path = report_dir / (
            f"event_brain_v002_h{horizon_sessions}_report.json"
        )
        artifact_path = artifact_dir / (
            f"event_brain_v002_h{horizon_sessions}_evaluation.pkl"
        )

        oos.to_csv(oos_path, index=False)
        report_path.write_text(
            json.dumps(report, indent=2, ensure_ascii=False),
            encoding="utf-8",
        )
        with artifact_path.open("wb") as fh:
            pickle.dump(
                {
                    "report": report,
                    "market_features": MARKET_FEATURES,
                    "market_categorical": MARKET_CATEGORICAL,
                    "event_features_numeric": EVENT_FEATURES_NUMERIC,
                    "event_features_categorical":
                        EVENT_FEATURES_CATEGORICAL,
                    "note":
                        "Evaluation artifact; candidate is not production.",
                },
                fh,
            )

        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                UPDATE event_brain_training_runs
                SET finished_at=?,
                    status='completed',
                    train_rows=?,
                    test_rows=?,
                    oof_rows=?,
                    artifact_path=?,
                    metrics_json=?
                WHERE training_run_id=?
                """,
                (
                    utc_now(),
                    int(max(x["train_rows"] for x in fold_summaries)),
                    len(oos),
                    len(oos),
                    str(artifact_path),
                    json.dumps(report, sort_keys=True),
                    training_run_id,
                ),
            )
            conn.commit()

        return {
            **report,
            "oos_predictions_path": str(oos_path),
            "report_path": str(report_path),
            "artifact_path": str(artifact_path),
        }

    except Exception as error:
        with sqlite3.connect(db) as conn:
            conn.execute(
                """
                UPDATE event_brain_training_runs
                SET finished_at=?,
                    status='failed',
                    error_json=?
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
    p = argparse.ArgumentParser(
        description=(
            "Event Brain v0.2: purged walk-forward with "
            "capacity-controlled incremental event benchmark"
        )
    )
    p.add_argument("--horizon-sessions", type=int, required=True)
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--artifact-dir", type=Path, default=DEFAULT_ARTIFACT_DIR)
    p.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    p.add_argument("--seed", type=int, default=42)
    p.add_argument("--outer-folds", type=int, default=4)
    p.add_argument("--bootstrap-reps", type=int, default=2000)
    args = p.parse_args()

    result = train_and_evaluate(
        args.db,
        args.horizon_sessions,
        artifact_dir=args.artifact_dir,
        report_dir=args.report_dir,
        seed=args.seed,
        outer_folds=args.outer_folds,
        bootstrap_reps=args.bootstrap_reps,
    )
    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

from __future__ import annotations

import json
from dataclasses import asdict
from pathlib import Path

import numpy as np
import pandas as pd
from sklearn.compose import ColumnTransformer
from sklearn.impute import SimpleImputer
from sklearn.linear_model import ElasticNet, HuberRegressor, Ridge
from sklearn.pipeline import Pipeline
from sklearn.preprocessing import OneHotEncoder, StandardScaler

import models.events.train_v002 as frozen_v002
from evaluation.events.robustness_v0021 import (
    BOOTSTRAP_REPS,
    BLOCK_LENGTHS,
    EARLY_OOS_INITIAL_FRACTION,
    INNER_FOLDS,
    OUTER_FOLDS,
    PRIMARY_INITIAL_FRACTION,
    RF_SEEDS,
    SIMPLE_FAMILIES,
    accession_cluster_bootstrap,
    add_fold_point_baselines,
    build_purged_group_folds,
    day_delta_autocorrelation,
    directional_baselines_by_fold,
    extreme_outcome_sensitivity,
    leave_one_group_out_sensitivity,
    moving_block_bootstrap,
    paired_point,
)
from evaluation.events.walkforward_v002 import PurgedFold
from models.events.dataset_v002 import (
    EVENT_FEATURES_CATEGORICAL,
    EVENT_FEATURES_NUMERIC,
    MARKET_CATEGORICAL,
    MARKET_FEATURES,
    TARGET,
)

PRIMARY_BASELINE_COL = "pred_capacity_control"
PRIMARY_CANDIDATE_COL = "pred_contextual_event"


def _dense_one_hot() -> OneHotEncoder:
    try:
        return OneHotEncoder(
            handle_unknown="ignore",
            sparse_output=False,
        )
    except TypeError:  # sklearn <1.2
        return OneHotEncoder(
            handle_unknown="ignore",
            sparse=False,
        )


def _simple_estimator(family: str):
    if family == "ridge":
        return Ridge(alpha=1.0)
    if family == "elasticnet":
        return ElasticNet(
            alpha=0.01,
            l1_ratio=0.5,
            max_iter=10000,
            selection="cyclic",
        )
    if family == "huber":
        return HuberRegressor(
            epsilon=1.35,
            alpha=0.0001,
            max_iter=1000,
        )
    raise ValueError(f"Familia no soportada: {family}")


def _simple_pipeline(
    numeric: list[str],
    categorical: list[str],
    family: str,
) -> Pipeline:
    transformers = [
        (
            "num",
            Pipeline(
                [
                    ("imputer", SimpleImputer(strategy="median")),
                    ("scale", StandardScaler()),
                ]
            ),
            numeric,
        )
    ]
    if categorical:
        transformers.append(
            (
                "cat",
                Pipeline(
                    [
                        (
                            "imputer",
                            SimpleImputer(
                                strategy="constant",
                                fill_value="unknown",
                            ),
                        ),
                        ("onehot", _dense_one_hot()),
                    ]
                ),
                categorical,
            )
        )

    prep = ColumnTransformer(
        transformers,
        remainder="drop",
        sparse_threshold=0.0,
    )
    return Pipeline(
        [
            ("prep", prep),
            ("model", _simple_estimator(family)),
        ]
    )


def _fit_market_rf(
    fit_frame: pd.DataFrame,
    validation_frame: pd.DataFrame,
    seed: int,
) -> np.ndarray:
    model = frozen_v002._pipeline(
        MARKET_FEATURES,
        MARKET_CATEGORICAL,
        seed,
    )
    model.fit(
        fit_frame[MARKET_FEATURES + MARKET_CATEGORICAL],
        fit_frame[TARGET],
    )
    return model.predict(
        validation_frame[MARKET_FEATURES + MARKET_CATEGORICAL]
    )


def _fit_market_simple(
    fit_frame: pd.DataFrame,
    validation_frame: pd.DataFrame,
    family: str,
) -> np.ndarray:
    model = _simple_pipeline(
        MARKET_FEATURES,
        MARKET_CATEGORICAL,
        family,
    )
    model.fit(
        fit_frame[MARKET_FEATURES + MARKET_CATEGORICAL],
        fit_frame[TARGET],
    )
    return model.predict(
        validation_frame[MARKET_FEATURES + MARKET_CATEGORICAL]
    )


def _inner_oof_market(
    train_df: pd.DataFrame,
    *,
    family: str,
    seed: int,
    group_column: str,
) -> pd.Series:
    folds = build_purged_group_folds(
        train_df,
        group_column=group_column,
        n_folds=INNER_FOLDS,
        initial_fraction=0.40,
        min_train_rows=35,
        min_test_rows=8,
    )
    out = pd.Series(index=train_df.index, dtype=float)
    for fold in folds:
        fit = train_df.loc[list(fold.train_index)]
        val = train_df.loc[list(fold.test_index)]
        if family == "rf":
            pred = _fit_market_rf(
                fit,
                val,
                seed + 1000 + fold.fold_id,
            )
        else:
            pred = _fit_market_simple(fit, val, family)
        out.loc[val.index] = np.asarray(pred, dtype=float)
    return out


def _fold_predictions_rf(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    *,
    seed: int,
    inner_group_column: str,
) -> dict[str, np.ndarray]:
    market_model = frozen_v002._pipeline(
        MARKET_FEATURES,
        MARKET_CATEGORICAL,
        seed,
    )
    event_only_model = frozen_v002._pipeline(
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

    oof = _inner_oof_market(
        train_df,
        family="rf",
        seed=seed + 200,
        group_column=inner_group_column,
    )
    usable = oof.dropna()
    residual = train_df.loc[usable.index].copy()
    residual["market_oof_prediction"] = usable
    residual["residual_target"] = (
        residual[TARGET] - residual["market_oof_prediction"]
    )
    if len(residual) < 45:
        raise RuntimeError(f"Residual OOF insuficiente: {len(residual)}")

    event_residual_model = frozen_v002._pipeline(
        EVENT_FEATURES_NUMERIC,
        EVENT_FEATURES_CATEGORICAL,
        seed + 300,
    )
    capacity_model = frozen_v002._pipeline(
        MARKET_FEATURES,
        MARKET_CATEGORICAL,
        seed + 400,
    )
    contextual_model = frozen_v002._pipeline(
        MARKET_FEATURES + EVENT_FEATURES_NUMERIC,
        MARKET_CATEGORICAL + EVENT_FEATURES_CATEGORICAL,
        seed + 500,
    )

    event_residual_model.fit(
        residual[
            EVENT_FEATURES_NUMERIC + EVENT_FEATURES_CATEGORICAL
        ],
        residual["residual_target"],
    )
    capacity_model.fit(
        residual[MARKET_FEATURES + MARKET_CATEGORICAL],
        residual["residual_target"],
    )
    contextual_model.fit(
        residual[
            MARKET_FEATURES
            + EVENT_FEATURES_NUMERIC
            + MARKET_CATEGORICAL
            + EVENT_FEATURES_CATEGORICAL
        ],
        residual["residual_target"],
    )

    market_pred = market_model.predict(
        test_df[MARKET_FEATURES + MARKET_CATEGORICAL]
    )
    event_only_pred = event_only_model.predict(
        test_df[EVENT_FEATURES_NUMERIC + EVENT_FEATURES_CATEGORICAL]
    )
    event_residual = event_residual_model.predict(
        test_df[EVENT_FEATURES_NUMERIC + EVENT_FEATURES_CATEGORICAL]
    )
    control_residual = capacity_model.predict(
        test_df[MARKET_FEATURES + MARKET_CATEGORICAL]
    )
    contextual_residual = contextual_model.predict(
        test_df[
            MARKET_FEATURES
            + EVENT_FEATURES_NUMERIC
            + MARKET_CATEGORICAL
            + EVENT_FEATURES_CATEGORICAL
        ]
    )
    return {
        "pred_zero": np.zeros(len(test_df)),
        "pred_market": market_pred,
        "pred_event_only": event_only_pred,
        "pred_market_plus_event": market_pred + event_residual,
        "pred_capacity_control": market_pred + control_residual,
        "pred_contextual_event": market_pred + contextual_residual,
    }


def _fold_predictions_simple(
    train_df: pd.DataFrame,
    test_df: pd.DataFrame,
    *,
    family: str,
    inner_group_column: str,
) -> dict[str, np.ndarray]:
    market_model = _simple_pipeline(
        MARKET_FEATURES,
        MARKET_CATEGORICAL,
        family,
    )
    event_only_model = _simple_pipeline(
        EVENT_FEATURES_NUMERIC,
        EVENT_FEATURES_CATEGORICAL,
        family,
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

    oof = _inner_oof_market(
        train_df,
        family=family,
        seed=0,
        group_column=inner_group_column,
    )
    usable = oof.dropna()
    residual = train_df.loc[usable.index].copy()
    residual["market_oof_prediction"] = usable
    residual["residual_target"] = (
        residual[TARGET] - residual["market_oof_prediction"]
    )
    if len(residual) < 45:
        raise RuntimeError(f"Residual OOF insuficiente: {len(residual)}")

    event_residual_model = _simple_pipeline(
        EVENT_FEATURES_NUMERIC,
        EVENT_FEATURES_CATEGORICAL,
        family,
    )
    capacity_model = _simple_pipeline(
        MARKET_FEATURES,
        MARKET_CATEGORICAL,
        family,
    )
    contextual_model = _simple_pipeline(
        MARKET_FEATURES + EVENT_FEATURES_NUMERIC,
        MARKET_CATEGORICAL + EVENT_FEATURES_CATEGORICAL,
        family,
    )
    event_residual_model.fit(
        residual[
            EVENT_FEATURES_NUMERIC + EVENT_FEATURES_CATEGORICAL
        ],
        residual["residual_target"],
    )
    capacity_model.fit(
        residual[MARKET_FEATURES + MARKET_CATEGORICAL],
        residual["residual_target"],
    )
    contextual_model.fit(
        residual[
            MARKET_FEATURES
            + EVENT_FEATURES_NUMERIC
            + MARKET_CATEGORICAL
            + EVENT_FEATURES_CATEGORICAL
        ],
        residual["residual_target"],
    )

    market_pred = market_model.predict(
        test_df[MARKET_FEATURES + MARKET_CATEGORICAL]
    )
    event_only_pred = event_only_model.predict(
        test_df[EVENT_FEATURES_NUMERIC + EVENT_FEATURES_CATEGORICAL]
    )
    event_residual = event_residual_model.predict(
        test_df[EVENT_FEATURES_NUMERIC + EVENT_FEATURES_CATEGORICAL]
    )
    control_residual = capacity_model.predict(
        test_df[MARKET_FEATURES + MARKET_CATEGORICAL]
    )
    contextual_residual = contextual_model.predict(
        test_df[
            MARKET_FEATURES
            + EVENT_FEATURES_NUMERIC
            + MARKET_CATEGORICAL
            + EVENT_FEATURES_CATEGORICAL
        ]
    )
    return {
        "pred_zero": np.zeros(len(test_df)),
        "pred_market": market_pred,
        "pred_event_only": event_only_pred,
        "pred_market_plus_event": market_pred + event_residual,
        "pred_capacity_control": market_pred + control_residual,
        "pred_contextual_event": market_pred + contextual_residual,
    }


def run_oos(
    frame: pd.DataFrame,
    folds: list[PurgedFold],
    *,
    family: str,
    seed: int = 42,
    inner_group_column: str = "event_id",
) -> tuple[pd.DataFrame, dict[int, pd.DataFrame]]:
    parts: list[pd.DataFrame] = []
    train_by_fold: dict[int, pd.DataFrame] = {}
    train_frames_in_order: list[pd.DataFrame] = []

    for fold in folds:
        train = frame.loc[list(fold.train_index)].copy()
        test = frame.loc[list(fold.test_index)].copy()
        train_by_fold[fold.fold_id] = train
        train_frames_in_order.append(train)

        if family == "rf":
            predictions = _fold_predictions_rf(
                train,
                test,
                seed=seed + fold.fold_id * 10000,
                inner_group_column=inner_group_column,
            )
        else:
            predictions = _fold_predictions_simple(
                train,
                test,
                family=family,
                inner_group_column=inner_group_column,
            )

        for name, values in predictions.items():
            test[name] = np.asarray(values, dtype=float)
        test["fold_id"] = fold.fold_id
        parts.append(test)

    oos = add_fold_point_baselines(parts, train_frames_in_order)
    oos = oos.sort_values(
        ["origin_trading_day", "state_time", "reaction_label_id"]
    )
    return oos, train_by_fold


def _metric_summary(oos: pd.DataFrame) -> dict[str, object]:
    model_cols = {
        "zero": "pred_zero",
        "train_mean": "pred_train_mean",
        "train_median": "pred_train_median",
        "market": "pred_market",
        "event_only": "pred_event_only",
        "market_plus_event": "pred_market_plus_event",
        "capacity_control": "pred_capacity_control",
        "contextual_event": "pred_contextual_event",
    }
    metrics = {
        name: frozen_v002._metrics(
            oos[TARGET].to_numpy(float),
            oos[col].to_numpy(float),
        )
        for name, col in model_cols.items()
    }
    return {
        "metrics": metrics,
        "primary_comparison": paired_point(
            oos,
            baseline_col=PRIMARY_BASELINE_COL,
            candidate_col=PRIMARY_CANDIDATE_COL,
        ),
    }


def evaluate_oos_robustness(
    oos: pd.DataFrame,
    *,
    bootstrap_reps: int = BOOTSTRAP_REPS,
    bootstrap_seed: int = 9102026,
) -> dict[str, object]:
    out = _metric_summary(oos)
    out["origin_day_bootstrap"] = frozen_v002._paired_comparison(
        oos,
        baseline_col=PRIMARY_BASELINE_COL,
        candidate_col=PRIMARY_CANDIDATE_COL,
        bootstrap_reps=bootstrap_reps,
        seed=bootstrap_seed,
    )
    out["accession_cluster_bootstrap"] = accession_cluster_bootstrap(
        oos,
        baseline_col=PRIMARY_BASELINE_COL,
        candidate_col=PRIMARY_CANDIDATE_COL,
        reps=bootstrap_reps,
        seed=bootstrap_seed + 1,
    )
    out["moving_block_bootstrap"] = {
        str(block): moving_block_bootstrap(
            oos,
            baseline_col=PRIMARY_BASELINE_COL,
            candidate_col=PRIMARY_CANDIDATE_COL,
            block_length=block,
            reps=bootstrap_reps,
            seed=bootstrap_seed + 100 + block,
        )
        for block in BLOCK_LENGTHS
    }
    out["paired_delta_origin_day_autocorrelation"] = (
        day_delta_autocorrelation(
            oos,
            baseline_col=PRIMARY_BASELINE_COL,
            candidate_col=PRIMARY_CANDIDATE_COL,
            max_lag=10,
        )
    )
    out["leave_one_asset_out"] = leave_one_group_out_sensitivity(
        oos,
        group_column="asset_id",
        baseline_col=PRIMARY_BASELINE_COL,
        candidate_col=PRIMARY_CANDIDATE_COL,
        min_group_rows=15,
    )
    out["leave_one_event_type_out"] = leave_one_group_out_sensitivity(
        oos,
        group_column="event_type",
        baseline_col=PRIMARY_BASELINE_COL,
        candidate_col=PRIMARY_CANDIDATE_COL,
        min_group_rows=30,
    )
    out["extreme_outcome_sensitivity"] = extreme_outcome_sensitivity(
        oos,
        baseline_col=PRIMARY_BASELINE_COL,
        candidate_col=PRIMARY_CANDIDATE_COL,
    )
    return out


def rf_seed_experiment(
    frame: pd.DataFrame,
    *,
    bootstrap_reps: int = BOOTSTRAP_REPS,
) -> tuple[dict[str, object], dict[int, pd.DataFrame]]:
    folds = build_purged_group_folds(
        frame,
        group_column="event_id",
        n_folds=OUTER_FOLDS,
        initial_fraction=PRIMARY_INITIAL_FRACTION,
        min_train_rows=80,
        min_test_rows=15,
    )
    results: dict[str, object] = {}
    oos_by_seed: dict[int, pd.DataFrame] = {}

    for seed in RF_SEEDS:
        oos, train_by_fold = run_oos(
            frame,
            folds,
            family="rf",
            seed=seed,
            inner_group_column="event_id",
        )
        oos_by_seed[seed] = oos
        summary = _metric_summary(oos)
        summary["directional_baselines"] = directional_baselines_by_fold(
            oos,
            train_by_fold,
        )
        summary["fold_deltas"] = {
            str(fold_id): paired_point(
                group,
                baseline_col=PRIMARY_BASELINE_COL,
                candidate_col=PRIMARY_CANDIDATE_COL,
            )
            for fold_id, group in oos.groupby("fold_id")
        }
        results[str(seed)] = summary

    seed_deltas = [
        results[str(seed)]["primary_comparison"][
            "mae_delta_baseline_minus_candidate_pct"
        ]
        for seed in RF_SEEDS
    ]
    aggregate = {
        "seeds": list(RF_SEEDS),
        "positive_seed_count": int(sum(delta > 0 for delta in seed_deltas)),
        "positive_seed_fraction": float(np.mean(np.array(seed_deltas) > 0)),
        "mean_delta_pct": float(np.mean(seed_deltas)),
        "median_delta_pct": float(np.median(seed_deltas)),
        "min_delta_pct": float(np.min(seed_deltas)),
        "max_delta_pct": float(np.max(seed_deltas)),
        "per_seed": results,
    }

    # Only seed 42 receives expensive dependence resampling here; structural
    # stages below provide additional independent sensitivity designs.
    aggregate["seed42_dependence_robustness"] = evaluate_oos_robustness(
        oos_by_seed[42],
        bootstrap_reps=bootstrap_reps,
        bootstrap_seed=420021,
    )
    return aggregate, oos_by_seed


def simple_family_experiment(
    frame: pd.DataFrame,
) -> dict[str, object]:
    folds = build_purged_group_folds(
        frame,
        group_column="event_id",
        n_folds=OUTER_FOLDS,
        initial_fraction=PRIMARY_INITIAL_FRACTION,
        min_train_rows=80,
        min_test_rows=15,
    )
    out: dict[str, object] = {}
    for family in SIMPLE_FAMILIES:
        oos, train_by_fold = run_oos(
            frame,
            folds,
            family=family,
            seed=42,
            inner_group_column="event_id",
        )
        summary = _metric_summary(oos)
        summary["directional_baselines"] = directional_baselines_by_fold(
            oos,
            train_by_fold,
        )
        summary["fold_deltas"] = {
            str(fold_id): paired_point(
                group,
                baseline_col=PRIMARY_BASELINE_COL,
                candidate_col=PRIMARY_CANDIDATE_COL,
            )
            for fold_id, group in oos.groupby("fold_id")
        }
        out[family] = summary
    return out


def structural_experiment(
    frame: pd.DataFrame,
    *,
    bootstrap_reps: int = BOOTSTRAP_REPS,
) -> dict[str, object]:
    accession_folds = build_purged_group_folds(
        frame,
        group_column="accession_number",
        n_folds=OUTER_FOLDS,
        initial_fraction=PRIMARY_INITIAL_FRACTION,
        min_train_rows=80,
        min_test_rows=15,
    )
    accession_oos, accession_train = run_oos(
        frame,
        accession_folds,
        family="rf",
        seed=42,
        inner_group_column="accession_number",
    )

    early_folds = build_purged_group_folds(
        frame,
        group_column="event_id",
        n_folds=OUTER_FOLDS,
        initial_fraction=EARLY_OOS_INITIAL_FRACTION,
        min_train_rows=80,
        min_test_rows=15,
    )
    early_oos, early_train = run_oos(
        frame,
        early_folds,
        family="rf",
        seed=42,
        inner_group_column="event_id",
    )

    return {
        "accession_grouped": {
            "outer_group": "accession_number",
            "inner_oof_group": "accession_number",
            "initial_fraction": PRIMARY_INITIAL_FRACTION,
            "folds": [asdict(x) | {
                "train_rows": len(x.train_index),
                "test_rows": len(x.test_index),
            } for x in accession_folds],
            "summary": _metric_summary(accession_oos),
            "directional_baselines": directional_baselines_by_fold(
                accession_oos,
                accession_train,
            ),
            "dependence_robustness": evaluate_oos_robustness(
                accession_oos,
                bootstrap_reps=bootstrap_reps,
                bootstrap_seed=7710021,
            ),
        },
        "early_oos": {
            "outer_group": "event_id",
            "inner_oof_group": "event_id",
            "initial_fraction": EARLY_OOS_INITIAL_FRACTION,
            "folds": [asdict(x) | {
                "train_rows": len(x.train_index),
                "test_rows": len(x.test_index),
            } for x in early_folds],
            "summary": _metric_summary(early_oos),
            "directional_baselines": directional_baselines_by_fold(
                early_oos,
                early_train,
            ),
        },
    }

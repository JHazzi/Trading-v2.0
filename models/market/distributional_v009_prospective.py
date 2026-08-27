from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any, Mapping

import joblib
import numpy as np
import pandas as pd

from models.market.distributional_v008_conditional_quantiles import (
    REQUIRED_QUANTILES,
    baseline_vol63_bundle,
    crossing_fraction,
    fit_probability_model,
    fit_quantile_models,
    monotone_rearrange,
    raw_model_standardized_predictions,
    reconstruct_return_bundle,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config" / "market_brain_distributional_v009.json"
DEFAULT_CORE_DB = ROOT / "data" / "processed" / "market_daily_v003_core.db"
DEFAULT_ARTIFACT_DIR = (
    ROOT / "models" / "market" / "artifacts"
    / "market_brain_distributional_v009"
)


def canonical_json(payload: Any) -> str:
    return json.dumps(
        payload,
        sort_keys=True,
        separators=(",", ":"),
        allow_nan=False,
    )


def sha256_json(payload: Any) -> str:
    return hashlib.sha256(canonical_json(payload).encode("utf-8")).hexdigest()


def file_sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def load_config(path: Path = DEFAULT_CONFIG) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg["version"] != "market_brain_distributional_v009_prospective_holdout_v001":
        raise ValueError("unexpected V009 version")
    if cfg["model_version"] != "market_brain_distributional_v009_hgb_own_state_static_v001":
        raise ValueError("unexpected V009 model version")
    if cfg["source_v0081_version"] != "market_brain_distributional_v0081_endogenous_closure_v001":
        raise ValueError("unexpected V008.1 source")
    if cfg["market_feature_version"] != "market_daily_state_v003_core":
        raise ValueError("market feature version changed")
    if cfg["label_version"] != "market_daily_reaction_v003_core":
        raise ValueError("label version changed")
    if cfg["target"] != "return_pct" or int(cfg["horizon_sessions"]) != 1:
        raise ValueError("V009 confirms only H1 return_pct")
    if tuple(float(x) for x in cfg["quantiles"]) != REQUIRED_QUANTILES:
        raise ValueError("V009 quantiles changed")
    if cfg["primary_candidate"] != "hgb_own_state_raw_static":
        raise ValueError("V009 candidate changed")
    if cfg["primary_reference"] != "vol63_raw_static":
        raise ValueError("V009 reference changed")
    if cfg["residual_scale_feature"] != "asset_vol_63d_pct":
        raise ValueError("V009 scale changed")
    if cfg["fit_policy"] != "single_pre_holdout_fit":
        raise ValueError("V009 requires one pre-holdout fit")
    if cfg["refit_during_confirmatory_window"] is not False:
        raise ValueError("V009 refit is forbidden during confirmation")
    if cfg["post_model_quantile_calibration"] != "none":
        raise ValueError("V009 post-model quantile calibration is forbidden")
    if cfg["probability_calibration"] != "none":
        raise ValueError("V009 probability calibration is forbidden")
    if len(cfg["frozen_own_features"]) != 14:
        raise ValueError("V009 own-state family changed")
    if int(cfg["confirmatory_origin_days"]) != 252:
        raise ValueError("V009 confirmatory horizon changed")
    if int(cfg["preliminary_descriptive_origin_days"]) != 126:
        raise ValueError("V009 preliminary checkpoint changed")
    if int(cfg["maximum_seal_delay_hours_from_state_time"]) > 16:
        raise ValueError("V009 seal window cannot be relaxed")
    if int(cfg["primary_bootstrap_block_length_origin_days"]) != 10:
        raise ValueError("V009 primary bootstrap block changed")
    if cfg.get("strict_historical_pit") is not False:
        raise ValueError("Core V003 is not strict PIT")
    for key in (
        "prospective_predictions_must_use_actual_seal_clock",
        "predictions_are_append_only",
        "outcomes_are_separate_from_predictions",
        "no_retroactive_prediction_backfill",
        "no_hyperparameter_selection",
        "no_refit_during_holdout",
        "no_posthoc_feature_change",
        "no_repeated_peeking_promotion",
    ):
        if cfg.get(key) is not True:
            raise ValueError(f"scientific guard disabled: {key}")
    for key in (
        "external_proxy_features_added",
        "event_features_added",
        "graph_features_added",
        "macro_features_added",
        "broker_cost_used_for_training",
    ):
        if cfg.get(key) is not False:
            raise ValueError(f"deferred information enabled: {key}")
    return cfg


def dataframe_sha256(frame: pd.DataFrame, columns: list[str]) -> str:
    work = frame.loc[:, columns].copy()
    header = canonical_json({
        "columns": columns,
        "dtypes": [str(work[name].dtype) for name in columns],
        "rows": int(len(work)),
    }).encode("utf-8")
    row_hashes = pd.util.hash_pandas_object(
        work,
        index=False,
        categorize=True,
    ).to_numpy(dtype="uint64")
    digest = hashlib.sha256()
    digest.update(header)
    digest.update(row_hashes.tobytes())
    return digest.hexdigest()


def _coerce_features(
    frame: pd.DataFrame,
    features: list[str],
) -> pd.DataFrame:
    frame = frame.copy()
    frame["asset_id"] = frame["asset_id"].astype("int64")
    for name in features:
        frame[name] = pd.to_numeric(frame[name], errors="coerce").astype("float64")
    values = frame[features].to_numpy(float)
    if np.isinf(values).any():
        raise RuntimeError("infinite feature values are forbidden")
    return frame


def load_training_frame(
    core_db: Path,
    cfg: Mapping[str, Any],
    universe_asset_ids: list[int],
) -> pd.DataFrame:
    if not universe_asset_ids:
        raise ValueError("empty frozen universe")
    features = list(cfg["frozen_own_features"])
    placeholders = ",".join("?" for _ in universe_asset_ids)
    feature_sql = ",\n              ".join(f"s.{name}" for name in features)
    params: list[Any] = [
        int(cfg["horizon_sessions"]),
        str(cfg["label_version"]),
        str(cfg["market_feature_version"]),
        str(cfg["not_before_origin_day"]),
        *[int(x) for x in universe_asset_ids],
    ]
    with sqlite3.connect(core_db) as conn:
        frame = pd.read_sql_query(
            f"""
            SELECT
              s.state_id,s.asset_id,s.ticker,s.sector,
              l.origin_trading_day,l.target_trading_day,l.return_pct,
              {feature_sql}
            FROM market_daily_v003_labels l
            JOIN market_daily_v003_states s ON s.state_id=l.state_id
            WHERE l.horizon_sessions=?
              AND l.label_status='usable'
              AND l.corporate_action_overlap=0
              AND l.label_version=?
              AND s.feature_version=?
              AND l.target_trading_day < ?
              AND s.asset_id IN ({placeholders})
            ORDER BY l.origin_trading_day,s.asset_id
            """,
            conn,
            params=params,
        )
    if frame.empty:
        raise RuntimeError("empty V009 training frame")
    frame = _coerce_features(frame, features)
    frame["return_pct"] = pd.to_numeric(
        frame["return_pct"], errors="raise"
    ).astype("float64")
    if not np.isfinite(frame["return_pct"].to_numpy(float)).all():
        raise RuntimeError("nonfinite training target")
    if (frame["target_trading_day"] >= str(cfg["not_before_origin_day"])).any():
        raise RuntimeError("training target crosses prospective start")
    if (frame["target_trading_day"] <= frame["origin_trading_day"]).any():
        raise RuntimeError("invalid training target clock")
    if frame.duplicated(["asset_id", "origin_trading_day"]).any():
        raise RuntimeError("duplicate training asset/origin rows")
    if frame["asset_id"].nunique() < int(cfg["minimum_predictions_per_origin"]):
        raise RuntimeError("insufficient frozen-universe training assets")
    return frame


def load_prediction_frame(
    core_db: Path,
    cfg: Mapping[str, Any],
    universe_asset_ids: list[int],
    origin_day: str,
) -> pd.DataFrame:
    if str(origin_day) < str(cfg["not_before_origin_day"]):
        raise RuntimeError("retroactive V009 origin is forbidden")
    features = list(cfg["frozen_own_features"])
    placeholders = ",".join("?" for _ in universe_asset_ids)
    feature_sql = ",\n              ".join(features)
    with sqlite3.connect(core_db) as conn:
        frame = pd.read_sql_query(
            f"""
            SELECT
              state_id,asset_id,ticker,sector,trading_day,state_time,
              state_point_in_time_verified,{feature_sql}
            FROM market_daily_v003_states
            WHERE feature_version=?
              AND trading_day=?
              AND asset_id IN ({placeholders})
            ORDER BY asset_id
            """,
            conn,
            params=[
                str(cfg["market_feature_version"]),
                str(origin_day),
                *[int(x) for x in universe_asset_ids],
            ],
        )
        if not frame.empty:
            state_ids = frame["state_id"].astype(str).tolist()
            state_placeholders = ",".join("?" for _ in state_ids)
            observed = conn.execute(
                f"""
                SELECT COUNT(*)
                FROM market_daily_v003_labels
                WHERE state_id IN ({state_placeholders})
                  AND horizon_sessions=?
                  AND label_version=?
                  AND label_status <> 'insufficient_future'
                """,
                [
                    *state_ids,
                    int(cfg["horizon_sessions"]),
                    str(cfg["label_version"]),
                ],
            ).fetchone()[0]
            if int(observed or 0) > 0:
                raise RuntimeError(
                    "H1 outcome is already observed for this origin; "
                    "retroactive prediction sealing is forbidden"
                )
    if frame.empty:
        raise RuntimeError(f"no V009 states for origin {origin_day}")
    frame = _coerce_features(frame, features)
    frame["origin_trading_day"] = frame["trading_day"].astype(str)
    if frame.duplicated(["asset_id", "origin_trading_day"]).any():
        raise RuntimeError("duplicate prediction asset/origin rows")
    if frame["state_time"].nunique() != 1:
        raise RuntimeError("origin state times are inconsistent")
    if len(frame) < int(cfg["minimum_predictions_per_origin"]):
        raise RuntimeError("prediction origin does not meet asset gate")
    scale = frame[str(cfg["residual_scale_feature"])].to_numpy(float)
    if np.any(np.isfinite(scale) & (scale < 0.0)):
        raise RuntimeError("negative V009 scale is invalid")
    return frame


def fit_static_artifact(
    training: pd.DataFrame,
    cfg: Mapping[str, Any],
    feature_manifest_sha256: str,
    fitted_at_utc: str,
) -> dict[str, Any]:
    features = list(cfg["frozen_own_features"])
    quantiles = tuple(float(x) for x in cfg["quantiles"])
    anchor, quantile_models = fit_quantile_models(
        training,
        features,
        str(cfg["residual_scale_feature"]),
        quantiles,
        cfg["fixed_model_profile"],
    )
    probability_model = fit_probability_model(
        training,
        features,
        cfg["fixed_model_profile"],
    )
    hash_columns = [
        "state_id",
        "asset_id",
        "origin_trading_day",
        "target_trading_day",
        "return_pct",
        *features,
    ]
    training_hash = dataframe_sha256(training, hash_columns)
    algorithm_contract = {
        "model_version": cfg["model_version"],
        "features": features,
        "quantiles": list(quantiles),
        "scale": cfg["residual_scale_feature"],
        "profile": cfg["fixed_model_profile"],
        "fit_policy": cfg["fit_policy"],
        "post_model_quantile_calibration": "none",
        "probability_calibration": "none",
        "crossing_policy": cfg["quantile_crossing_policy"],
    }
    return {
        "artifact_version": cfg["model_version"],
        "experiment_version": cfg["version"],
        "fitted_at_utc": fitted_at_utc,
        "features": features,
        "quantiles": quantiles,
        "scale_feature": str(cfg["residual_scale_feature"]),
        "feature_manifest_sha256": feature_manifest_sha256,
        "algorithm_contract": algorithm_contract,
        "algorithm_contract_sha256": sha256_json(algorithm_contract),
        "training_data_sha256": training_hash,
        "training_summary": {
            "rows": int(len(training)),
            "assets": int(training["asset_id"].nunique()),
            "origin_days": int(training["origin_trading_day"].nunique()),
            "first_origin_day": str(training["origin_trading_day"].min()),
            "last_origin_day": str(training["origin_trading_day"].max()),
            "last_target_day": str(training["target_trading_day"].max()),
        },
        "anchor": anchor,
        "quantile_models": quantile_models,
        "probability_model": probability_model,
    }


def save_artifact(payload: Mapping[str, Any], path: Path) -> str:
    path.parent.mkdir(parents=True, exist_ok=True)
    if path.exists():
        raise FileExistsError(
            f"refusing to overwrite frozen V009 artifact: {path}"
        )
    temporary = path.with_suffix(path.suffix + ".building")
    joblib.dump(dict(payload), temporary, compress=3)
    temporary.replace(path)
    return file_sha256(path)


def load_artifact(
    path: Path,
    cfg: Mapping[str, Any],
    feature_manifest_sha256: str,
) -> dict[str, Any]:
    payload = joblib.load(path)
    if payload.get("artifact_version") != cfg["model_version"]:
        raise RuntimeError("V009 artifact model version mismatch")
    if payload.get("experiment_version") != cfg["version"]:
        raise RuntimeError("V009 artifact experiment mismatch")
    if payload.get("features") != list(cfg["frozen_own_features"]):
        raise RuntimeError("V009 artifact feature drift")
    if tuple(payload.get("quantiles", ())) != tuple(
        float(x) for x in cfg["quantiles"]
    ):
        raise RuntimeError("V009 artifact quantile drift")
    if payload.get("feature_manifest_sha256") != feature_manifest_sha256:
        raise RuntimeError("V009 artifact manifest mismatch")
    return payload


def predict_static_distributions(
    payload: Mapping[str, Any],
    states: pd.DataFrame,
) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any]]:
    features = list(payload["features"])
    scale_feature = str(payload["scale_feature"])
    raw, usable = raw_model_standardized_predictions(
        payload["quantile_models"],
        states,
        features,
        scale_feature,
    )
    pre_crossing = crossing_fraction(raw, usable)
    standardized = monotone_rearrange(raw, usable)
    candidate = reconstruct_return_bundle(
        standardized,
        usable,
        payload["anchor"],
        states,
        scale_feature,
    )
    probability = payload["probability_model"].predict_proba(
        states[features].to_numpy(float)
    )[:, 1]
    candidate["probability_positive"] = np.clip(
        probability, 0.0, 1.0
    )
    reference = baseline_vol63_bundle(
        payload["anchor"],
        states,
        scale_feature,
        payload["quantiles"],
    )
    diagnostics = {
        "raw_quantile_crossing_fraction": float(pre_crossing),
        "positive_scale_rows": int(usable.sum()),
        "fallback_rows": int((~usable).sum()),
        "post_model_quantile_calibration": "none",
        "probability_calibration": "none",
    }
    return candidate, reference, diagnostics

from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path
from typing import Any

import numpy as np

from evaluation.market.daily_v003_benchmark import (
    build_purged_day_folds,
    fold_summary,
)
from models.market.distributional_v006_baselines import (
    DEFAULT_CONFIG,
    DEFAULT_CORE_DB,
    load_config,
    load_horizon,
)

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_PREREGISTRATION = (
    ROOT
    / "reports"
    / "market_brain_daily_v003"
    / "benchmark_v0011"
    / "preregistered_inputs.json"
)


def _frozen_core_hash(preregistration: Path) -> str:
    payload = json.loads(preregistration.read_text(encoding="utf-8"))
    return str(payload["core_db"]["sha256"])


def _sha256(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        for chunk in iter(lambda: handle.read(8 * 1024 * 1024), b""):
            digest.update(chunk)
    return digest.hexdigest()


def audit(
    core_db: Path = DEFAULT_CORE_DB,
    config_path: Path = DEFAULT_CONFIG,
    preregistration: Path = DEFAULT_PREREGISTRATION,
) -> dict[str, Any]:
    cfg = load_config(config_path)
    failures: list[str] = []
    reviews: list[str] = []

    if not core_db.exists():
        raise FileNotFoundError(core_db)
    frozen_hash = _frozen_core_hash(preregistration)
    actual_hash = _sha256(core_db)
    actual_size = int(core_db.stat().st_size)
    if frozen_hash != str(cfg["source_core_db_sha256"]):
        failures.append("source_core_hash_not_frozen_v003_hash")
    if actual_hash != str(cfg["source_core_db_sha256"]):
        failures.append("source_core_file_hash_mismatch")
    if actual_size != int(cfg["source_core_db_size_bytes"]):
        failures.append("source_core_file_size_mismatch")

    with sqlite3.connect(core_db) as conn:
        tables = {
            str(row[0]) for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type='table'"
            )
        }
        required_tables = {
            "build_metadata",
            "market_daily_v003_states",
            "market_daily_v003_labels",
        }
        if not required_tables.issubset(tables):
            failures.append("required_core_tables_missing")

        h1_path_vol_nonzero = int(conn.execute(
            """
            SELECT COUNT(*)
            FROM market_daily_v003_labels
            WHERE horizon_sessions=1
              AND label_status='usable'
              AND ABS(realized_path_vol_pct) > 1e-12
            """
        ).fetchone()[0])
        if h1_path_vol_nonzero:
            failures.append("h1_path_volatility_not_zero")

        mixed_feature_versions = int(conn.execute(
            """
            SELECT COUNT(DISTINCT feature_version)
            FROM market_daily_v003_states
            """
        ).fetchone()[0])
        mixed_label_versions = int(conn.execute(
            """
            SELECT COUNT(DISTINCT label_version)
            FROM market_daily_v003_labels
            """
        ).fetchone()[0])
        if mixed_feature_versions != 1:
            failures.append("mixed_market_feature_versions")
        if mixed_label_versions != 1:
            failures.append("mixed_label_versions")

    horizon_audits: dict[str, Any] = {}
    for horizon in cfg["horizons_sessions"]:
        frame = load_horizon(core_db, int(horizon), cfg)
        folds = build_purged_day_folds(
            frame,
            n_folds=int(cfg["outer_folds"]),
            initial_fraction=float(cfg["initial_fraction"]),
        )
        rows = int(len(frame))
        assets = int(frame["asset_id"].nunique())
        origin_days = int(frame["origin_trading_day"].nunique())
        nonpositive_scale = int(np.sum(frame["scale_value"].to_numpy(float) <= 0.0))
        strict_pit_rows = int(
            np.sum(frame["state_point_in_time_verified"].astype(int) == 1)
        )

        if rows < int(cfg["minimum_rows_per_horizon"]):
            failures.append(f"h{horizon}_rows_below_gate")
        if assets < int(cfg["minimum_assets"]):
            failures.append(f"h{horizon}_assets_below_gate")
        if origin_days < int(cfg["minimum_origin_days"]):
            failures.append(f"h{horizon}_origin_days_below_gate")
        if strict_pit_rows:
            failures.append(f"h{horizon}_unexpected_strict_pit_rows")

        horizon_audits[str(horizon)] = {
            "rows": rows,
            "assets": assets,
            "origin_days": origin_days,
            "first_origin_day": str(frame["origin_trading_day"].min()),
            "last_origin_day": str(frame["origin_trading_day"].max()),
            "nonpositive_scale_rows": nonpositive_scale,
            "nonpositive_scale_fraction": float(nonpositive_scale / rows),
            "strict_pit_rows": strict_pit_rows,
            "folds": fold_summary(folds),
        }

    reviews.extend([
        "historical_price_reconstruction_is_not_strict_pit",
        "current_equity_cohort_is_not_survivorship_free",
        "raw_close_target_excludes_corporate_action_overlap_windows",
        "terminal_return_distribution_is_not_yet_a_path_distribution",
    ])
    return {
        "status": "FAIL" if failures else "PASS",
        "failures": failures,
        "reviews": reviews,
        "benchmark_version": cfg["version"],
        "model_version": cfg["model_version"],
        "dataset_contract": cfg["dataset_contract"],
        "market_feature_version": cfg["market_feature_version"],
        "label_version": cfg["label_version"],
        "target": cfg["target"],
        "quantiles": cfg["quantiles"],
        "source_core_db": {
            "path": str(core_db),
            "size_bytes": actual_size,
            "actual_sha256": actual_hash,
            "frozen_sha256": frozen_hash,
            "matches_preregistered_v003_hash": (
                actual_hash
                == frozen_hash
                == str(cfg["source_core_db_sha256"])
            ),
            "matches_preregistered_size": (
                actual_size == int(cfg["source_core_db_size_bytes"])
            ),
        },
        "horizons": horizon_audits,
        "feature_contract": {
            "model_visible_market_features": [cfg["scale_feature"]],
            "outcome_columns_model_visible": False,
            "event_features": False,
            "graph_features": False,
            "macro_features": False,
            "broker_cost_in_training": False,
        },
        "score_contract": {
            "primary": cfg["primary_score"],
            "quantile_score": "pinball_loss",
            "probability_score": "positive_return_brier",
            "uncertainty": (
                "noncircular_moving_block_bootstrap_on_origin_day_losses"
            ),
        },
        "main_db_mutated": False,
        "core_db_mutated": False,
        "next_gate": (
            "A PASS permits the frozen empirical benchmark at all four "
            "horizons; it is not a predictability result. Interpret the "
            "complete benchmark before preregistering learned models."
        ),
    }

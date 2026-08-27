from __future__ import annotations

import sqlite3
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Mapping

import numpy as np
import pandas as pd

from evaluation.market.distributional_v006 import (
    daily_loss_comparison,
    distribution_metrics,
    mean_pinball_rows,
    moving_block_bootstrap_daily_loss,
    pinball_rows,
    quantile_name,
)
from storage.prospective_registry import (
    canonical_json,
    connect_registry,
    insert_outcome_and_scores,
    sha256_json,
    stable_id,
)


def utc_now_text() -> str:
    return datetime.now(timezone.utc).isoformat()


def _scalar_pinball(actual: float, prediction: float, q: float) -> float:
    error = float(actual) - float(prediction)
    return float(max(float(q) * error, (float(q) - 1.0) * error))


def score_prediction(
    prediction: Mapping[str, Any],
    outcome_id: str,
    actual: float,
    scored_at_utc: str,
) -> dict[str, Any]:
    quantiles = {
        0.05: float(prediction["q05"]),
        0.25: float(prediction["q25"]),
        0.50: float(prediction["q50"]),
        0.75: float(prediction["q75"]),
        0.95: float(prediction["q95"]),
    }
    losses = {
        q: _scalar_pinball(actual, value, q)
        for q, value in quantiles.items()
    }
    payload = {
        "prediction_id": str(prediction["prediction_id"]),
        "outcome_id": outcome_id,
        "mean_pinball_loss": float(np.mean(list(losses.values()))),
        "pinball_q05": losses[0.05],
        "pinball_q25": losses[0.25],
        "pinball_q50": losses[0.50],
        "pinball_q75": losses[0.75],
        "pinball_q95": losses[0.95],
        "median_absolute_error": abs(actual - quantiles[0.50]),
        "brier_positive": (
            float(prediction["probability_positive"])
            - float(actual > 0.0)
        ) ** 2,
        "hit_q05": int(actual <= quantiles[0.05]),
        "hit_q25": int(actual <= quantiles[0.25]),
        "hit_q50": int(actual <= quantiles[0.50]),
        "hit_q75": int(actual <= quantiles[0.75]),
        "hit_q95": int(actual <= quantiles[0.95]),
        "scored_at_utc": scored_at_utc,
    }
    payload["payload_sha256"] = sha256_json(payload)
    return payload


def settle_available_outcomes(
    registry_db: Path,
    core_db: Path,
    cfg: Mapping[str, Any],
    observed_at_utc: str | None = None,
) -> dict[str, Any]:
    observed_at_utc = observed_at_utc or utc_now_text()
    with connect_registry(registry_db) as conn:
        unresolved = pd.read_sql_query(
            """
            SELECT b.batch_id,b.origin_trading_day,p.asset_id,p.state_id,
                   p.prediction_id,p.model_role,p.q05,p.q25,p.q50,p.q75,p.q95,
                   p.probability_positive
            FROM prospective_prediction_batches b
            JOIN prospective_distribution_predictions p
              ON p.batch_id=b.batch_id
            LEFT JOIN prospective_prediction_outcomes o
              ON o.batch_id=b.batch_id AND o.asset_id=p.asset_id
            WHERE b.experiment_version=?
              AND o.outcome_id IS NULL
            ORDER BY b.origin_trading_day,p.asset_id,p.model_role
            """,
            conn,
            params=(str(cfg["version"]),),
        )
    if unresolved.empty:
        return {
            "status": "NO_AVAILABLE_UNRESOLVED_PREDICTIONS",
            "outcomes_linked": 0,
            "scores_linked": 0,
        }

    origins = sorted(unresolved["origin_trading_day"].astype(str).unique())
    placeholders = ",".join("?" for _ in origins)
    with sqlite3.connect(core_db) as conn:
        labels = pd.read_sql_query(
            f"""
            SELECT
              l.label_id,l.state_id,l.asset_id,l.origin_trading_day,
              l.target_trading_day,l.horizon_sessions,l.return_pct,
              l.mfe_pct,l.mae_pct,l.realized_path_vol_pct,
              l.corporate_action_overlap,l.label_status,l.label_version
            FROM market_daily_v003_labels l
            WHERE l.origin_trading_day IN ({placeholders})
              AND l.horizon_sessions=?
              AND l.label_version=?
              AND l.label_status <> 'insufficient_future'
            ORDER BY l.origin_trading_day,l.asset_id
            """,
            conn,
            params=[
                *origins,
                int(cfg["horizon_sessions"]),
                str(cfg["label_version"]),
            ],
        )
    if labels.empty:
        return {
            "status": "WAITING_FOR_OUTCOMES",
            "outcomes_linked": 0,
            "scores_linked": 0,
        }

    predictions = {
        (str(key[0]), int(key[1])): group
        for key, group in unresolved.groupby(
            ["origin_trading_day", "asset_id"], sort=False
        )
    }
    linked = 0
    scores_linked = 0
    skipped = 0
    for label in labels.itertuples(index=False):
        key = (str(label.origin_trading_day), int(label.asset_id))
        group = predictions.get(key)
        if group is None:
            continue
        if group["state_id"].astype(str).nunique() != 1:
            raise RuntimeError("candidate/reference state identity mismatch")
        if str(group.iloc[0]["state_id"]) != str(label.state_id):
            raise RuntimeError("outcome state identity differs from sealed prediction")

        outcome_payload = {
            "batch_id": str(group.iloc[0]["batch_id"]),
            "asset_id": int(label.asset_id),
            "origin_trading_day": str(label.origin_trading_day),
            "target_trading_day": str(label.target_trading_day),
            "horizon_sessions": int(label.horizon_sessions),
            "label_version": str(label.label_version),
            "label_status": str(label.label_status),
            "corporate_action_overlap": int(label.corporate_action_overlap),
            "return_pct": (
                float(label.return_pct)
                if pd.notna(label.return_pct)
                else None
            ),
            "mfe_pct": float(label.mfe_pct) if pd.notna(label.mfe_pct) else None,
            "mae_pct": float(label.mae_pct) if pd.notna(label.mae_pct) else None,
            "realized_path_vol_pct": (
                float(label.realized_path_vol_pct)
                if pd.notna(label.realized_path_vol_pct)
                else None
            ),
            "observed_at_utc": observed_at_utc,
            "source_label_id": (
                str(label.label_id) if pd.notna(label.label_id) else None
            ),
        }
        outcome_id = stable_id("outcome", outcome_payload)
        outcome_payload["outcome_id"] = outcome_id
        outcome_payload["payload_sha256"] = sha256_json(outcome_payload)

        scores: list[dict[str, Any]] = []
        if (
            str(label.label_status) == "usable"
            and int(label.corporate_action_overlap) == 0
            and pd.notna(label.return_pct)
        ):
            actual = float(label.return_pct)
            for prediction in group.to_dict("records"):
                scores.append(
                    score_prediction(
                        prediction,
                        outcome_id,
                        actual,
                        observed_at_utc,
                    )
                )
        else:
            skipped += 1

        result = insert_outcome_and_scores(
            registry_db,
            outcome_payload,
            scores,
        )
        if result == "linked":
            linked += 1
            scores_linked += len(scores)

    return {
        "status": "SETTLED_AVAILABLE",
        "outcomes_linked": int(linked),
        "scores_linked": int(scores_linked),
        "nonusable_outcomes_linked": int(skipped),
        "origins_checked": origins,
    }


def _load_scored_frame(
    registry_db: Path,
    experiment_version: str,
) -> pd.DataFrame:
    with connect_registry(registry_db) as conn:
        frame = pd.read_sql_query(
            """
            SELECT
              b.origin_trading_day,p.asset_id,p.model_role,
              p.q05,p.q25,p.q50,p.q75,p.q95,p.probability_positive,
              o.return_pct,o.label_status,o.corporate_action_overlap,
              s.mean_pinball_loss,s.pinball_q05,s.pinball_q25,
              s.pinball_q50,s.pinball_q75,s.pinball_q95,
              s.median_absolute_error,s.brier_positive
            FROM prospective_prediction_batches b
            JOIN prospective_distribution_predictions p
              ON p.batch_id=b.batch_id
            JOIN prospective_prediction_outcomes o
              ON o.batch_id=b.batch_id AND o.asset_id=p.asset_id
            JOIN prospective_distribution_scores s
              ON s.prediction_id=p.prediction_id
            WHERE b.experiment_version=?
              AND o.label_status='usable'
              AND o.corporate_action_overlap=0
            ORDER BY b.origin_trading_day,p.asset_id,p.model_role
            """,
            conn,
            params=(experiment_version,),
        )
    return frame


def _batch_audit(
    registry_db: Path,
    cfg: Mapping[str, Any],
) -> pd.DataFrame:
    with connect_registry(registry_db) as conn:
        return pd.read_sql_query(
            """
            SELECT
              b.batch_id,b.origin_trading_day,b.predicted_assets,
              COUNT(DISTINCT CASE WHEN p.model_role='candidate'
                    THEN p.asset_id END) AS candidate_assets,
              COUNT(DISTINCT CASE WHEN p.model_role='reference'
                    THEN p.asset_id END) AS reference_assets,
              COUNT(DISTINCT o.asset_id) AS resolved_assets,
              COUNT(DISTINCT CASE WHEN o.label_status='usable'
                    AND o.corporate_action_overlap=0
                    THEN o.asset_id END) AS usable_assets
            FROM prospective_prediction_batches b
            JOIN prospective_distribution_predictions p
              ON p.batch_id=b.batch_id
            LEFT JOIN prospective_prediction_outcomes o
              ON o.batch_id=b.batch_id AND o.asset_id=p.asset_id
            WHERE b.experiment_version=?
            GROUP BY b.batch_id,b.origin_trading_day,b.predicted_assets
            ORDER BY b.origin_trading_day
            """,
            conn,
            params=(str(cfg["version"]),),
        )


def _continuity_audit(
    core_db: Path,
    cfg: Mapping[str, Any],
    universe_asset_ids: list[int],
    sealed_days: list[str],
) -> dict[str, Any]:
    if not sealed_days:
        return {
            "eligible_state_days": [],
            "missing_sealed_days": [],
            "status": "WAITING",
        }
    placeholders = ",".join("?" for _ in universe_asset_ids)
    with sqlite3.connect(core_db) as conn:
        rows = conn.execute(
            f"""
            SELECT trading_day,COUNT(DISTINCT asset_id)
            FROM market_daily_v003_states
            WHERE feature_version=?
              AND trading_day BETWEEN ? AND ?
              AND asset_id IN ({placeholders})
            GROUP BY trading_day
            HAVING COUNT(DISTINCT asset_id) >= ?
            ORDER BY trading_day
            """,
            [
                str(cfg["market_feature_version"]),
                sealed_days[0],
                sealed_days[-1],
                *[int(x) for x in universe_asset_ids],
                int(cfg["minimum_predictions_per_origin"]),
            ],
        ).fetchall()
    eligible = [str(row[0]) for row in rows]
    missing = sorted(set(eligible) - set(sealed_days))
    extra = sorted(set(sealed_days) - set(eligible))
    return {
        "eligible_state_days": eligible,
        "sealed_days": sealed_days,
        "missing_sealed_days": missing,
        "sealed_days_without_eligible_state": extra,
        "status": "PASS" if not missing and not extra else "FAIL",
    }


def _mean_abs_calibration(metrics: Mapping[str, Any]) -> float:
    return float(np.mean([
        abs(float(value["calibration_error"]))
        for value in metrics["per_quantile"].values()
    ]))


def _time_block_table(
    paired: pd.DataFrame,
    days: list[str],
    blocks: int,
) -> pd.DataFrame:
    rows = []
    for block_id, day_values in enumerate(np.array_split(np.asarray(days), blocks)):
        selected = paired[
            paired["origin_trading_day"].isin(
                [str(x) for x in day_values.tolist()]
            )
        ]
        rows.append({
            "block_id": int(block_id),
            "first_origin_day": str(day_values[0]),
            "last_origin_day": str(day_values[-1]),
            "origin_days": int(len(day_values)),
            "rows": int(len(selected)),
            "mean_delta_pct": float(
                (
                    selected["reference_mean_pinball_loss"]
                    - selected["candidate_mean_pinball_loss"]
                ).mean()
            ),
        })
    return pd.DataFrame(rows)


def evaluate_prospective(
    registry_db: Path,
    core_db: Path,
    cfg: Mapping[str, Any],
    universe_asset_ids: list[int],
) -> tuple[dict[str, Any], dict[str, pd.DataFrame]]:
    batches = _batch_audit(registry_db, cfg)
    if batches.empty:
        return {
            "benchmark_version": cfg["version"],
            "evaluation_version": cfg["evaluation_version"],
            "status": "WAITING_FOR_FIRST_SEALED_ORIGIN",
            "sealed_origin_days": 0,
            "formal_gate_available": False,
        }, {}

    sealed_days = batches["origin_trading_day"].astype(str).tolist()
    batches["resolved_fraction"] = (
        batches["resolved_assets"] / batches["predicted_assets"]
    )
    batches["usable_fraction"] = (
        batches["usable_assets"] / batches["predicted_assets"]
    )
    batches["role_count_match"] = (
        (batches["candidate_assets"] == batches["predicted_assets"])
        & (batches["reference_assets"] == batches["predicted_assets"])
    )
    required_days = int(cfg["confirmatory_origin_days"])
    formal_batches = batches.iloc[:required_days].copy()
    continuity_days = (
        formal_batches["origin_trading_day"].astype(str).tolist()
        if len(formal_batches) == required_days
        else sealed_days
    )
    continuity = _continuity_audit(
        core_db,
        cfg,
        universe_asset_ids,
        continuity_days,
    )
    resolved_ready = bool(
        len(formal_batches) == required_days
        and (
            formal_batches["resolved_fraction"]
            >= float(cfg["minimum_resolved_fraction_per_origin"])
        ).all()
        and (
            formal_batches["usable_fraction"]
            >= float(cfg["minimum_usable_fraction_per_origin"])
        ).all()
        and formal_batches["role_count_match"].all()
    )

    preliminary_days = int(cfg["preliminary_descriptive_origin_days"])
    preliminary_batches = batches.iloc[:preliminary_days]
    preliminary_ready = bool(
        len(preliminary_batches) == preliminary_days
        and (
            preliminary_batches["resolved_fraction"]
            >= float(cfg["minimum_resolved_fraction_per_origin"])
        ).all()
        and (
            preliminary_batches["usable_fraction"]
            >= float(cfg["minimum_usable_fraction_per_origin"])
        ).all()
        and preliminary_batches["role_count_match"].all()
    )
    if continuity["status"] != "PASS":
        status = "INTEGRITY_FAIL_MISSING_OR_EXTRA_ORIGIN_BATCH"
    elif len(batches) < preliminary_days:
        status = "ACCUMULATING_UNTOUCHED_HOLDOUT"
    elif not preliminary_ready:
        status = "WAITING_FOR_PRELIMINARY_OUTCOMES"
    elif len(batches) < required_days:
        status = "PRELIMINARY_DESCRIPTIVE_NO_PROMOTION"
    elif not resolved_ready:
        status = "WAITING_FOR_CONFIRMATORY_OUTCOMES_OR_COVERAGE"
    else:
        status = "READY_FOR_FIXED_CONFIRMATORY_GATE"

    report: dict[str, Any] = {
        "benchmark_version": cfg["version"],
        "evaluation_version": cfg["evaluation_version"],
        "status": status,
        "sealed_origin_days": int(len(batches)),
        "resolved_origin_days": int(
            (
                batches["resolved_fraction"]
                >= float(cfg["minimum_resolved_fraction_per_origin"])
            ).sum()
        ),
        "first_sealed_origin_day": str(sealed_days[0]),
        "last_sealed_origin_day": str(sealed_days[-1]),
        "confirmatory_origin_days_required": required_days,
        "preliminary_origin_days": preliminary_days,
        "formal_gate_available": resolved_ready and continuity["status"] == "PASS",
        "continuity_audit": continuity,
        "batch_coverage": {
            "scope": (
                "formal_first_252"
                if len(formal_batches) == required_days
                else "all_accumulated_before_formal_gate"
            ),
            "minimum_resolved_fraction": float(
                formal_batches["resolved_fraction"].min()
            ),
            "minimum_usable_fraction": float(
                formal_batches["usable_fraction"].min()
            ),
            "all_candidate_reference_counts_match": bool(
                formal_batches["role_count_match"].all()
            ),
        },
        "cohort_policy": cfg["confirmatory_cohort_policy"],
        "no_repeated_peeking_promotion": True,
        "claim_boundary": cfg["claim_boundary"],
    }
    tables: dict[str, pd.DataFrame] = {"batch_audit": batches}
    if not preliminary_ready or continuity["status"] != "PASS":
        return report, tables

    scored = _load_scored_frame(registry_db, str(cfg["version"]))
    pivot = scored.pivot(
        index=["origin_trading_day", "asset_id", "return_pct"],
        columns="model_role",
        values=[
            "q05","q25","q50","q75","q95","probability_positive",
            "mean_pinball_loss",
        ],
    )
    pivot.columns = [f"{role}_{metric}" for metric, role in pivot.columns]
    paired = pivot.reset_index()
    cohort_size = required_days if resolved_ready else preliminary_days
    cohort_days = sealed_days[:cohort_size]
    paired = paired[
        paired["origin_trading_day"].astype(str).isin(cohort_days)
    ].copy()
    expected = {
        f"{role}_{metric}"
        for role in ("candidate", "reference")
        for metric in (
            "q05","q25","q50","q75","q95",
            "probability_positive","mean_pinball_loss",
        )
    }
    missing = sorted(expected - set(paired.columns))
    if missing:
        raise RuntimeError(f"paired V009 score columns missing: {missing}")

    actual = paired["return_pct"].to_numpy(float)
    candidate = {
        "quantiles": {
            q: paired[f"candidate_{quantile_name(q)}"].to_numpy(float)
            for q in (0.05, 0.25, 0.5, 0.75, 0.95)
        },
        "probability_positive": paired[
            "candidate_probability_positive"
        ].to_numpy(float),
    }
    reference = {
        "quantiles": {
            q: paired[f"reference_{quantile_name(q)}"].to_numpy(float)
            for q in (0.05, 0.25, 0.5, 0.75, 0.95)
        },
        "probability_positive": paired[
            "reference_probability_positive"
        ].to_numpy(float),
    }
    candidate_loss = mean_pinball_rows(actual, candidate["quantiles"])
    reference_loss = mean_pinball_rows(actual, reference["quantiles"])
    daily = daily_loss_comparison(
        paired["origin_trading_day"],
        reference_loss,
        candidate_loss,
    )
    bootstrap = {
        str(block): moving_block_bootstrap_daily_loss(
            daily,
            block_length=int(block),
            reps=int(cfg["bootstrap_reps"]),
            seed=int(cfg["bootstrap_seed"]),
        )
        for block in cfg["moving_block_lengths_origin_days"]
    }
    candidate_metrics = distribution_metrics(
        actual,
        candidate["quantiles"],
        candidate["probability_positive"],
    )
    reference_metrics = distribution_metrics(
        actual,
        reference["quantiles"],
        reference["probability_positive"],
    )
    per_quantile = {}
    for q in (0.05, 0.25, 0.5, 0.75, 0.95):
        qdaily = daily_loss_comparison(
            paired["origin_trading_day"],
            pinball_rows(actual, reference["quantiles"][q], q),
            pinball_rows(actual, candidate["quantiles"][q], q),
        )
        per_quantile[quantile_name(q)] = {
            "quantile": q,
            "origin_day_equal_weight_delta_pct": float(
                qdaily["loss_delta_baseline_minus_candidate"].mean()
            ),
        }
    time_blocks = _time_block_table(
        paired,
        cohort_days,
        int(cfg["time_blocks"]),
    )
    tables.update({
        "paired_scores": paired,
        "daily_losses": daily,
        "time_blocks": time_blocks,
    })
    report["fixed_cohort_metrics"] = {
        "cohort_kind": (
            "confirmatory_first_252"
            if resolved_ready
            else "preliminary_first_126_descriptive"
        ),
        "origin_days": int(len(cohort_days)),
        "first_origin_day": cohort_days[0],
        "last_origin_day": cohort_days[-1],
        "rows": int(len(paired)),
        "origin_day_equal_weight_delta_pct": float(
            daily["loss_delta_baseline_minus_candidate"].mean()
        ),
        "moving_block_bootstrap": bootstrap,
        "candidate_metrics": candidate_metrics,
        "reference_metrics": reference_metrics,
        "per_quantile": per_quantile,
        "time_blocks": time_blocks.to_dict("records"),
    }
    if not resolved_ready:
        report["status"] = "PRELIMINARY_DESCRIPTIVE_NO_PROMOTION"
        report["formal_gate_available"] = False
        return report, tables

    primary_block = str(
        int(cfg["primary_bootstrap_block_length_origin_days"])
    )
    ci = [
        float(x)
        for x in bootstrap[primary_block]["ci95"]
    ]
    candidate_calibration = _mean_abs_calibration(candidate_metrics)
    reference_calibration = _mean_abs_calibration(reference_metrics)
    improved_quantiles = sum(
        float(value["origin_day_equal_weight_delta_pct"]) > 0.0
        for value in per_quantile.values()
    )
    positive_blocks = int(
        (time_blocks["mean_delta_pct"].to_numpy(float) > 0.0).sum()
    )
    checks = {
        "primary_score_ci_positive": ci[0] > 0.0,
        "candidate_calibration_not_worse": (
            candidate_calibration <= reference_calibration
        ),
        "minimum_positive_time_blocks_met": (
            positive_blocks >= int(cfg["minimum_positive_time_blocks"])
        ),
        "minimum_improved_quantiles_met": (
            improved_quantiles >= int(cfg["minimum_improved_quantiles"])
        ),
        "continuity_pass": continuity["status"] == "PASS",
        "coverage_pass": resolved_ready,
    }
    required_checks = list(checks.values())
    if all(required_checks):
        gate_status = "PASS_PROSPECTIVE_MARKET_DISTRIBUTION_CONFIRMED"
    elif ci[1] < 0.0:
        gate_status = "FAIL_PROSPECTIVE_SIGNIFICANT"
    else:
        gate_status = "INCONCLUSIVE_PROSPECTIVE_NO_PROMOTION"
    report["status"] = gate_status
    report["formal_gate_available"] = True
    report["confirmatory_gate"] = {
        "status": gate_status,
        "primary_block_length_origin_days": int(primary_block),
        "primary_ci95": ci,
        "candidate_mean_abs_quantile_calibration_error": candidate_calibration,
        "reference_mean_abs_quantile_calibration_error": reference_calibration,
        "positive_time_blocks": positive_blocks,
        "improved_quantiles": int(improved_quantiles),
        "checks": checks,
        "confirmed_alpha": False,
        "confirmed_claim": (
            "market-only H1 terminal-return distribution improvement "
            "over frozen raw vol63"
        ),
    }
    return report, tables

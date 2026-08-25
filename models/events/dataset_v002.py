from __future__ import annotations

import sqlite3
from pathlib import Path
from typing import Iterable

import numpy as np
import pandas as pd


EVENT_FEATURES_NUMERIC = [
    "evidence_count",
    "distinct_cluster_count",
    "distinct_source_count",
    "point_in_time_evidence_fraction",
    "semantic_observed_fact_count",
    "semantic_official_statement_count",
    "semantic_reported_fact_count",
    "semantic_opinion_count",
    "semantic_forecast_count",
    "semantic_rumor_count",
    "semantic_speculation_count",
    "semantic_correction_count",
    "semantic_retraction_count",
    "semantic_mixed_count",
    "semantic_unknown_count",
    "seconds_since_first_evidence",
    "event_age_seconds_filled",
    "time_to_scheduled_seconds_filled",
    "has_known_occurrence_time",
    "has_scheduled_time",
]

EVENT_FEATURES_CATEGORICAL = [
    "event_type",
    "event_subtype_filled",
    "event_scope",
    "source_signature",
]

ASSET_STATE_FEATURES = [
    "asset_return_1d_pct",
    "asset_return_3d_pct",
    "asset_return_5d_pct",
    "asset_return_10d_pct",
    "asset_return_20d_pct",
    "asset_vol_5d_pct",
    "asset_vol_20d_pct",
    "asset_range_1d_pct",
    "asset_distance_high_20d_pct",
    "asset_distance_low_20d_pct",
    "asset_volume_ratio_20d",
]

CROSS_SECTION_FEATURES = [
    "cross_section_peer_count",
    "cross_section_median_return_1d_pct",
    "cross_section_median_return_5d_pct",
    "cross_section_median_return_20d_pct",
    "cross_section_breadth_positive_1d",
    "cross_section_breadth_positive_5d",
    "cross_section_dispersion_1d_pct",
    "cross_section_median_vol_20d_pct",
    "asset_minus_cross_section_1d_pct",
    "asset_minus_cross_section_5d_pct",
    "asset_minus_cross_section_20d_pct",
]

SECTOR_FEATURES = [
    "sector_peer_count",
    "sector_context_fallback",
    "sector_median_return_1d_pct",
    "sector_median_return_5d_pct",
    "sector_median_return_20d_pct",
    "sector_median_vol_20d_pct",
    "asset_minus_sector_1d_pct",
    "asset_minus_sector_5d_pct",
    "asset_minus_sector_20d_pct",
]

MARKET_FEATURES = (
    ASSET_STATE_FEATURES
    + CROSS_SECTION_FEATURES
    + SECTOR_FEATURES
)

MARKET_CATEGORICAL = ["sector_filled"]

TARGET = "return_pct"
MARKET_FEATURE_VERSION = "daily_asset_cross_section_sector_v002_leave_one_out"
EVENT_FEATURE_VERSION = "event_state_v002"
LABEL_VERSION = "event_reaction_daily_v002"


def _parse_float(value: object) -> float:
    result = float(value)
    if not np.isfinite(result):
        raise ValueError(f"Valor no finito: {value!r}")
    return result


def _universe_asset_ids(
    conn: sqlite3.Connection,
    event_feature_version: str,
) -> list[int]:
    rows = conn.execute(
        """
        SELECT DISTINCT asset_id
        FROM normalized_event_state_snapshots
        WHERE feature_version=?
        ORDER BY asset_id
        """,
        (event_feature_version,),
    ).fetchall()
    return [int(row[0]) for row in rows]


def _histories_asof(
    conn: sqlite3.Connection,
    *,
    universe_asset_ids: list[int],
    state_time: str,
    origin_day: str,
    max_sessions: int = 21,
) -> dict[int, dict[str, object]]:
    if not universe_asset_ids:
        return {}

    placeholders = ",".join("?" for _ in universe_asset_ids)
    rows = conn.execute(
        f"""
        WITH latest_observation AS (
            SELECT
                g.asset_id,
                g.trading_day,
                g.close,
                g.high,
                g.low,
                g.volume,
                g.observation_sequence,
                g.observed_at,
                g.price_observation_id,
                ROW_NUMBER() OVER (
                    PARTITION BY g.asset_id, g.trading_day
                    ORDER BY
                        g.observation_sequence DESC,
                        julianday(g.observed_at) DESC,
                        g.price_observation_id DESC
                ) AS obs_rank
            FROM daily_price_quality_gated_observations_v001 AS g
            WHERE g.asset_id IN ({placeholders})
              AND g.trading_day <= ?
              AND julianday(g.available_at) <= julianday(?)
        ),
        ranked_days AS (
            SELECT
                lo.*,
                ROW_NUMBER() OVER (
                    PARTITION BY lo.asset_id
                    ORDER BY lo.trading_day DESC
                ) AS day_rank
            FROM latest_observation AS lo
            WHERE lo.obs_rank=1
        )
        SELECT
            rd.asset_id,
            a.ticker,
            a.sector,
            rd.trading_day,
            rd.close,
            rd.high,
            rd.low,
            rd.volume
        FROM ranked_days AS rd
        JOIN assets AS a
          ON a.asset_id=rd.asset_id
        WHERE rd.day_rank <= ?
        ORDER BY rd.asset_id, rd.trading_day
        """,
        [*universe_asset_ids, origin_day, state_time, max_sessions],
    ).fetchall()

    out: dict[int, dict[str, object]] = {}
    for asset_id, ticker, sector, day, close, high, low, volume in rows:
        bucket = out.setdefault(
            int(asset_id),
            {
                "ticker": str(ticker),
                "sector": str(sector or "unknown"),
                "rows": [],
            },
        )
        bucket["rows"].append(
            {
                "day": str(day),
                "close": _parse_float(close),
                "high": _parse_float(high),
                "low": _parse_float(low),
                "volume": (
                    None if volume is None else float(volume)
                ),
            }
        )
    return out


def _asset_state(
    rows: list[dict[str, object]],
) -> dict[str, float] | None:
    if len(rows) < 21:
        return None

    close = np.array([float(x["close"]) for x in rows], dtype=float)
    high = np.array([float(x["high"]) for x in rows], dtype=float)
    low = np.array([float(x["low"]) for x in rows], dtype=float)
    volume = np.array(
        [
            np.nan if x["volume"] is None else float(x["volume"])
            for x in rows
        ],
        dtype=float,
    )

    if np.any(~np.isfinite(close[-21:])) or close[-1] <= 0:
        return None

    def ret(n: int) -> float:
        return 100.0 * (close[-1] / close[-1 - n] - 1.0)

    daily = 100.0 * (close[1:] / close[:-1] - 1.0)
    mean_vol = np.nanmean(volume[-20:])
    volume_ratio = (
        float(volume[-1] / mean_vol)
        if np.isfinite(volume[-1])
        and np.isfinite(mean_vol)
        and mean_vol > 0
        else 1.0
    )

    return {
        "asset_return_1d_pct": ret(1),
        "asset_return_3d_pct": ret(3),
        "asset_return_5d_pct": ret(5),
        "asset_return_10d_pct": ret(10),
        "asset_return_20d_pct": ret(20),
        "asset_vol_5d_pct": float(np.std(daily[-5:], ddof=0)),
        "asset_vol_20d_pct": float(np.std(daily[-20:], ddof=0)),
        "asset_range_1d_pct": float(
            100.0 * (high[-1] - low[-1]) / close[-1]
        ),
        "asset_distance_high_20d_pct": float(
            100.0 * (close[-1] / np.max(high[-20:]) - 1.0)
        ),
        "asset_distance_low_20d_pct": float(
            100.0 * (close[-1] / np.min(low[-20:]) - 1.0)
        ),
        "asset_volume_ratio_20d": volume_ratio,
    }


def _median(
    feature_by_asset: dict[int, dict[str, float]],
    ids: Iterable[int],
    feature: str,
) -> float:
    values = np.array(
        [feature_by_asset[int(asset_id)][feature] for asset_id in ids],
        dtype=float,
    )
    if len(values) == 0:
        raise ValueError("No hay peers para mediana")
    return float(np.median(values))


def _context_features(
    histories: dict[int, dict[str, object]],
    target_asset_id: int,
) -> tuple[dict[str, float], str] | None:
    feature_by_asset: dict[int, dict[str, float]] = {}
    sector_by_asset: dict[int, str] = {}

    for asset_id, info in histories.items():
        features = _asset_state(info["rows"])
        if features is None:
            continue
        feature_by_asset[int(asset_id)] = features
        sector_by_asset[int(asset_id)] = str(info["sector"] or "unknown")

    own = feature_by_asset.get(target_asset_id)
    if own is None:
        return None

    peers = sorted(
        asset_id
        for asset_id in feature_by_asset
        if asset_id != target_asset_id
    )
    if len(peers) < 3:
        return None

    cross_1d = np.array(
        [feature_by_asset[x]["asset_return_1d_pct"] for x in peers],
        dtype=float,
    )
    cross_5d = np.array(
        [feature_by_asset[x]["asset_return_5d_pct"] for x in peers],
        dtype=float,
    )
    cross_20d = np.array(
        [feature_by_asset[x]["asset_return_20d_pct"] for x in peers],
        dtype=float,
    )
    cross_vol20 = np.array(
        [feature_by_asset[x]["asset_vol_20d_pct"] for x in peers],
        dtype=float,
    )

    market = {
        "cross_section_peer_count": float(len(peers)),
        "cross_section_median_return_1d_pct": float(
            np.median(cross_1d)
        ),
        "cross_section_median_return_5d_pct": float(
            np.median(cross_5d)
        ),
        "cross_section_median_return_20d_pct": float(
            np.median(cross_20d)
        ),
        "cross_section_breadth_positive_1d": float(
            np.mean(cross_1d > 0)
        ),
        "cross_section_breadth_positive_5d": float(
            np.mean(cross_5d > 0)
        ),
        "cross_section_dispersion_1d_pct": float(
            np.std(cross_1d, ddof=0)
        ),
        "cross_section_median_vol_20d_pct": float(
            np.median(cross_vol20)
        ),
    }
    market["asset_minus_cross_section_1d_pct"] = (
        own["asset_return_1d_pct"]
        - market["cross_section_median_return_1d_pct"]
    )
    market["asset_minus_cross_section_5d_pct"] = (
        own["asset_return_5d_pct"]
        - market["cross_section_median_return_5d_pct"]
    )
    market["asset_minus_cross_section_20d_pct"] = (
        own["asset_return_20d_pct"]
        - market["cross_section_median_return_20d_pct"]
    )

    sector = sector_by_asset.get(target_asset_id, "unknown")
    sector_peers = [
        asset_id
        for asset_id in peers
        if sector_by_asset.get(asset_id, "unknown") == sector
    ]

    if sector_peers:
        sector_context = {
            "sector_peer_count": float(len(sector_peers)),
            "sector_context_fallback": 0.0,
            "sector_median_return_1d_pct": _median(
                feature_by_asset,
                sector_peers,
                "asset_return_1d_pct",
            ),
            "sector_median_return_5d_pct": _median(
                feature_by_asset,
                sector_peers,
                "asset_return_5d_pct",
            ),
            "sector_median_return_20d_pct": _median(
                feature_by_asset,
                sector_peers,
                "asset_return_20d_pct",
            ),
            "sector_median_vol_20d_pct": _median(
                feature_by_asset,
                sector_peers,
                "asset_vol_20d_pct",
            ),
        }
    else:
        # Missing sector coverage is an observed data limitation, not an
        # economic assumption. Fall back to cross section and expose a flag.
        sector_context = {
            "sector_peer_count": 0.0,
            "sector_context_fallback": 1.0,
            "sector_median_return_1d_pct":
                market["cross_section_median_return_1d_pct"],
            "sector_median_return_5d_pct":
                market["cross_section_median_return_5d_pct"],
            "sector_median_return_20d_pct":
                market["cross_section_median_return_20d_pct"],
            "sector_median_vol_20d_pct":
                market["cross_section_median_vol_20d_pct"],
        }

    sector_context["asset_minus_sector_1d_pct"] = (
        own["asset_return_1d_pct"]
        - sector_context["sector_median_return_1d_pct"]
    )
    sector_context["asset_minus_sector_5d_pct"] = (
        own["asset_return_5d_pct"]
        - sector_context["sector_median_return_5d_pct"]
    )
    sector_context["asset_minus_sector_20d_pct"] = (
        own["asset_return_20d_pct"]
        - sector_context["sector_median_return_20d_pct"]
    )

    return {**own, **market, **sector_context}, sector


def load_dataset(
    db: Path,
    horizon_sessions: int,
    *,
    event_feature_version: str = EVENT_FEATURE_VERSION,
    label_version: str = LABEL_VERSION,
) -> pd.DataFrame:
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        universe_asset_ids = _universe_asset_ids(
            conn,
            event_feature_version,
        )
        if not universe_asset_ids:
            return pd.DataFrame()

        rows = conn.execute(
            """
            SELECT
                l.reaction_label_id,
                l.event_state_id,
                l.event_id,
                l.asset_id,
                l.state_time,
                l.origin_trading_day,
                l.target_trading_day,
                l.return_pct,
                s.*
            FROM normalized_event_reaction_labels AS l
            JOIN normalized_event_state_snapshots AS s
              ON s.event_state_id=l.event_state_id
            WHERE l.horizon_sessions=?
              AND l.label_version=?
              AND l.label_status='usable'
              AND s.feature_version=?
            ORDER BY
                l.origin_trading_day,
                julianday(l.state_time),
                l.reaction_label_id
            """,
            (
                horizon_sessions,
                label_version,
                event_feature_version,
            ),
        ).fetchall()

        cache: dict[
            tuple[str, str],
            dict[int, dict[str, object]],
        ] = {}
        data: list[dict[str, object]] = []

        for row in rows:
            state_time = str(row["state_time"])
            origin_day = str(row["origin_trading_day"])
            cache_key = (state_time, origin_day)
            if cache_key not in cache:
                cache[cache_key] = _histories_asof(
                    conn,
                    universe_asset_ids=universe_asset_ids,
                    state_time=state_time,
                    origin_day=origin_day,
                )

            context = _context_features(
                cache[cache_key],
                int(row["asset_id"]),
            )
            if context is None:
                continue
            market, sector = context

            item: dict[str, object] = {
                "reaction_label_id": str(row["reaction_label_id"]),
                "event_state_id": str(row["event_state_id"]),
                "event_id": str(row["event_id"]),
                "asset_id": int(row["asset_id"]),
                "state_time": state_time,
                "origin_trading_day": origin_day,
                "target_trading_day": str(row["target_trading_day"]),
                TARGET: float(row["return_pct"]),
                "event_type": str(row["event_type"]),
                "event_subtype_filled": (
                    str(row["event_subtype"])
                    if row["event_subtype"]
                    else "unknown"
                ),
                "event_scope": str(row["event_scope"]),
                "source_signature": str(row["source_signature"]),
                "sector_filled": sector or "unknown",
                "event_age_seconds_filled": (
                    float(row["event_age_seconds"])
                    if row["event_age_seconds"] is not None
                    else 0.0
                ),
                "time_to_scheduled_seconds_filled": (
                    float(row["time_to_scheduled_seconds"])
                    if row["time_to_scheduled_seconds"] is not None
                    else 0.0
                ),
            }

            for name in EVENT_FEATURES_NUMERIC:
                if name in {
                    "event_age_seconds_filled",
                    "time_to_scheduled_seconds_filled",
                }:
                    continue
                item[name] = float(row[name])

            item.update(market)
            data.append(item)

    frame = pd.DataFrame(data)
    if frame.empty:
        return frame

    frame["event_anchor_day"] = frame.groupby(
        "event_id"
    )["origin_trading_day"].transform("min")
    return frame

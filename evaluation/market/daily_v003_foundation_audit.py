from __future__ import annotations

import json
import sqlite3
from collections import Counter
from pathlib import Path
from statistics import median
from typing import Any

from features.market.daily_v003_contract import (
    DESIRED_MARKET_PROXIES,
    DESIRED_RATE_CREDIT_PROXIES,
    DESIRED_SECTOR_PROXIES,
    DESIRED_VOLATILITY_PROXIES,
    MIN_CROSS_SECTION_ASSETS,
    MIN_OWN_HISTORY_DAYS,
    as_dict as contract_dict,
)

QUALITY_VIEW = "daily_price_quality_gated_observations_v001"


def _objects(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type = ?",
            (kind,),
        )
    }


def _columns(conn: sqlite3.Connection, name: str) -> list[str]:
    try:
        return [
            str(row[1])
            for row in conn.execute(f"PRAGMA table_info({name})")
        ]
    except sqlite3.DatabaseError:
        return []


def _scalar(conn: sqlite3.Connection, sql: str, params=()) -> Any:
    row = conn.execute(sql, params).fetchone()
    return None if row is None else row[0]


def _rows(conn: sqlite3.Connection, sql: str, params=()) -> list[sqlite3.Row]:
    return list(conn.execute(sql, params).fetchall())


def _quantiles(values: list[int]) -> dict[str, float | int | None]:
    if not values:
        return {
            "min": None,
            "p10": None,
            "p25": None,
            "median": None,
            "p75": None,
            "p90": None,
            "max": None,
        }

    vals = sorted(values)

    def q(p: float) -> float:
        if len(vals) == 1:
            return float(vals[0])
        pos = (len(vals) - 1) * p
        lo = int(pos)
        hi = min(lo + 1, len(vals) - 1)
        weight = pos - lo
        return float(vals[lo] * (1 - weight) + vals[hi] * weight)

    return {
        "min": int(vals[0]),
        "p10": q(0.10),
        "p25": q(0.25),
        "median": q(0.50),
        "p75": q(0.75),
        "p90": q(0.90),
        "max": int(vals[-1]),
    }


def _asset_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    rows = _rows(
        conn,
        """
        SELECT
            asset_type,
            COALESCE(sector, '<NULL>') AS sector,
            COALESCE(country, '<NULL>') AS country,
            COALESCE(exchange, '<NULL>') AS exchange,
            active,
            COUNT(*) AS n
        FROM assets
        GROUP BY asset_type, sector, country, exchange, active
        """,
    )
    return {
        "assets_total": int(_scalar(conn, "SELECT COUNT(*) FROM assets") or 0),
        "active_assets": int(
            _scalar(conn, "SELECT COUNT(*) FROM assets WHERE active = 1") or 0
        ),
        "active_equities": int(
            _scalar(
                conn,
                """
                SELECT COUNT(*) FROM assets
                WHERE active = 1 AND asset_type = 'equity'
                """,
            )
            or 0
        ),
        "sectors_active_equity": {
            str(row["sector"]): int(row["n"])
            for row in _rows(
                conn,
                """
                SELECT COALESCE(sector, '<NULL>') AS sector, COUNT(*) AS n
                FROM assets
                WHERE active = 1 AND asset_type = 'equity'
                GROUP BY COALESCE(sector, '<NULL>')
                ORDER BY n DESC, sector
                """,
            )
        },
        "asset_type_counts": {
            str(row["asset_type"]): int(row["n"])
            for row in _rows(
                conn,
                """
                SELECT asset_type, COUNT(*) AS n
                FROM assets
                GROUP BY asset_type
                ORDER BY n DESC
                """,
            )
        },
        "detailed_group_rows": [dict(row) for row in rows],
    }


def _daily_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    coverage_rows = _rows(
        conn,
        f"""
        SELECT
            a.asset_id,
            a.ticker,
            a.asset_type,
            a.active,
            COALESCE(a.sector, '<NULL>') AS sector,
            COUNT(DISTINCT q.trading_day) AS trading_days,
            COUNT(*) AS observation_rows,
            MIN(q.trading_day) AS first_day,
            MAX(q.trading_day) AS last_day,
            SUM(CASE WHEN q.observation_point_in_time_verified = 1
                     THEN 1 ELSE 0 END) AS pit_rows,
            COUNT(*) - COUNT(DISTINCT q.trading_day) AS extra_observations
        FROM assets a
        LEFT JOIN {QUALITY_VIEW} q
          ON q.asset_id = a.asset_id
        GROUP BY
            a.asset_id, a.ticker, a.asset_type, a.active,
            COALESCE(a.sector, '<NULL>')
        ORDER BY trading_days DESC, a.ticker
        """,
    )

    values = [int(row["trading_days"] or 0) for row in coverage_rows]
    thresholds = {}
    for threshold in (21, 63, 126, 252, 504, 1260, 2000, 2500):
        thresholds[str(threshold)] = int(
            sum(value >= threshold for value in values)
        )

    availability_basis = {
        str(row["availability_basis"]): int(row["n"])
        for row in _rows(
            conn,
            f"""
            SELECT availability_basis, COUNT(*) AS n
            FROM {QUALITY_VIEW}
            GROUP BY availability_basis
            ORDER BY n DESC
            """,
        )
    }

    revision_days = int(
        _scalar(
            conn,
            f"""
            SELECT COUNT(*)
            FROM (
                SELECT asset_id, trading_day
                FROM {QUALITY_VIEW}
                GROUP BY asset_id, trading_day
                HAVING COUNT(*) > 1
            )
            """,
        )
        or 0
    )

    return {
        "quality_view": QUALITY_VIEW,
        "observation_rows": int(
            _scalar(conn, f"SELECT COUNT(*) FROM {QUALITY_VIEW}") or 0
        ),
        "unique_asset_days": int(
            _scalar(
                conn,
                f"""
                SELECT COUNT(*)
                FROM (
                    SELECT asset_id, trading_day
                    FROM {QUALITY_VIEW}
                    GROUP BY asset_id, trading_day
                )
                """,
            )
            or 0
        ),
        "assets_with_daily_data": int(
            _scalar(
                conn,
                f"SELECT COUNT(DISTINCT asset_id) FROM {QUALITY_VIEW}",
            )
            or 0
        ),
        "first_day": _scalar(
            conn, f"SELECT MIN(trading_day) FROM {QUALITY_VIEW}"
        ),
        "last_day": _scalar(
            conn, f"SELECT MAX(trading_day) FROM {QUALITY_VIEW}"
        ),
        "strict_pit_observation_rows": int(
            _scalar(
                conn,
                f"""
                SELECT COUNT(*) FROM {QUALITY_VIEW}
                WHERE observation_point_in_time_verified = 1
                """,
            )
            or 0
        ),
        "availability_basis_counts": availability_basis,
        "asset_days_with_multiple_observations": revision_days,
        "assets_by_minimum_history_days": thresholds,
        "trading_days_distribution_all_assets": _quantiles(values),
        "per_asset": [dict(row) for row in coverage_rows],
    }


def _eligible_panel_by_day(conn: sqlite3.Connection) -> dict[str, Any]:
    # Use one row per asset-day. The foundation audit is about coverage,
    # not selecting a revised state for model features yet.
    rows = _rows(
        conn,
        f"""
        WITH unique_days AS (
            SELECT q.asset_id, q.trading_day
            FROM {QUALITY_VIEW} q
            JOIN assets a ON a.asset_id = q.asset_id
            WHERE a.active = 1 AND a.asset_type = 'equity'
            GROUP BY q.asset_id, q.trading_day
        ),
        ranked AS (
            SELECT
                asset_id,
                trading_day,
                ROW_NUMBER() OVER (
                    PARTITION BY asset_id
                    ORDER BY trading_day
                ) AS own_history_days
            FROM unique_days
        )
        SELECT
            trading_day,
            SUM(CASE WHEN own_history_days >= 63 THEN 1 ELSE 0 END)
                AS eligible_63,
            SUM(CASE WHEN own_history_days >= ? THEN 1 ELSE 0 END)
                AS eligible_253
        FROM ranked
        GROUP BY trading_day
        ORDER BY trading_day
        """,
        (MIN_OWN_HISTORY_DAYS,),
    )

    if not rows:
        return {
            "days": 0,
            "first_day_with_50_assets_253": None,
            "first_day_with_100_assets_253": None,
            "first_day_with_300_assets_253": None,
            "latest": None,
        }

    def first_day(threshold: int) -> str | None:
        for row in rows:
            if int(row["eligible_253"] or 0) >= threshold:
                return str(row["trading_day"])
        return None

    latest = rows[-1]
    return {
        "days": len(rows),
        "first_day_with_50_assets_253": first_day(50),
        "first_day_with_100_assets_253": first_day(100),
        "first_day_with_300_assets_253": first_day(300),
        "first_day_with_min_cross_section_assets_253": first_day(
            MIN_CROSS_SECTION_ASSETS
        ),
        "latest": {
            "trading_day": str(latest["trading_day"]),
            "eligible_63": int(latest["eligible_63"] or 0),
            "eligible_253": int(latest["eligible_253"] or 0),
        },
    }


def _sector_readiness_latest(conn: sqlite3.Connection) -> dict[str, Any]:
    latest = _scalar(conn, f"SELECT MAX(trading_day) FROM {QUALITY_VIEW}")
    if latest is None:
        return {"latest_day": None, "sectors": {}}

    rows = _rows(
        conn,
        f"""
        WITH unique_days AS (
            SELECT q.asset_id, q.trading_day
            FROM {QUALITY_VIEW} q
            JOIN assets a ON a.asset_id = q.asset_id
            WHERE a.active = 1 AND a.asset_type = 'equity'
              AND q.trading_day <= ?
            GROUP BY q.asset_id, q.trading_day
        ),
        histories AS (
            SELECT asset_id, COUNT(*) AS n
            FROM unique_days
            GROUP BY asset_id
            HAVING COUNT(*) >= ?
        )
        SELECT COALESCE(a.sector, '<NULL>') AS sector, COUNT(*) AS assets
        FROM histories h
        JOIN assets a ON a.asset_id = h.asset_id
        GROUP BY COALESCE(a.sector, '<NULL>')
        ORDER BY assets DESC, sector
        """,
        (latest, MIN_OWN_HISTORY_DAYS),
    )
    return {
        "latest_day": str(latest),
        "sectors": {str(r["sector"]): int(r["assets"]) for r in rows},
    }


def _proxy_coverage(conn: sqlite3.Connection) -> dict[str, Any]:
    tickers = list(
        dict.fromkeys(
            DESIRED_MARKET_PROXIES
            + DESIRED_SECTOR_PROXIES
            + DESIRED_VOLATILITY_PROXIES
            + DESIRED_RATE_CREDIT_PROXIES
        )
    )
    out = {}
    for ticker in tickers:
        asset = conn.execute(
            """
            SELECT asset_id, ticker, asset_type, active, sector
            FROM assets WHERE ticker = ?
            """,
            (ticker,),
        ).fetchone()
        if asset is None:
            out[ticker] = {
                "asset_exists": False,
                "quality_gated_daily_days": 0,
            }
            continue
        days = int(
            _scalar(
                conn,
                f"""
                SELECT COUNT(DISTINCT trading_day)
                FROM {QUALITY_VIEW}
                WHERE asset_id = ?
                """,
                (asset["asset_id"],),
            )
            or 0
        )
        out[ticker] = {
            "asset_exists": True,
            "asset_id": int(asset["asset_id"]),
            "asset_type": str(asset["asset_type"]),
            "active": int(asset["active"]),
            "sector": asset["sector"],
            "quality_gated_daily_days": days,
        }
    return out


def _legacy_price_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = _objects(conn, "table")
    if "price_bars" not in tables:
        return {"present": False}
    rows = _rows(
        conn,
        """
        SELECT
            interval,
            COUNT(*) AS rows,
            COUNT(DISTINCT asset_id) AS assets,
            MIN(timestamp) AS first_timestamp,
            MAX(timestamp) AS last_timestamp
        FROM price_bars
        GROUP BY interval
        ORDER BY rows DESC
        """,
    )
    return {
        "present": True,
        "by_interval": [dict(row) for row in rows],
    }


def _macro_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = _objects(conn, "table")
    if "macro_observations" not in tables:
        return {
            "present": False,
            "causal_vintage_contract": False,
        }

    columns = set(_columns(conn, "macro_observations"))
    # Legacy macro_observations has observation_time but no explicit
    # publication/retrieval/available-at version contract.
    causal = (
        {"available_at", "observed_at"} <= columns
        or {"release_available_at", "observed_at"} <= columns
    )

    return {
        "present": True,
        "rows": int(
            _scalar(conn, "SELECT COUNT(*) FROM macro_observations") or 0
        ),
        "symbols": int(
            _scalar(
                conn,
                "SELECT COUNT(DISTINCT symbol) FROM macro_observations",
            )
            or 0
        ),
        "first_observation_time": _scalar(
            conn,
            "SELECT MIN(observation_time) FROM macro_observations",
        ),
        "last_observation_time": _scalar(
            conn,
            "SELECT MAX(observation_time) FROM macro_observations",
        ),
        "columns": sorted(columns),
        "causal_vintage_contract": bool(causal),
        "usable_for_market_v003_features_now": bool(causal),
        "note": (
            "Legacy macro rows are not admitted into Market V003 until "
            "publication/vintage/availability semantics are explicit."
        ),
    }


def _corporate_action_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = _objects(conn, "table")
    if "corporate_action_observations" not in tables:
        return {"present": False}

    rows = _rows(
        conn,
        """
        SELECT action_type, COUNT(*) AS rows,
               COUNT(DISTINCT asset_id) AS assets
        FROM corporate_action_observations
        GROUP BY action_type
        ORDER BY rows DESC
        """,
    )
    return {
        "present": True,
        "rows": int(
            _scalar(
                conn, "SELECT COUNT(*) FROM corporate_action_observations"
            )
            or 0
        ),
        "by_type": [dict(row) for row in rows],
    }


def _universe_summary(conn: sqlite3.Connection) -> dict[str, Any]:
    tables = _objects(conn, "table")
    candidates = [
        name
        for name in (
            "asset_universe_membership",
            "asset_universe_memberships",
            "universe_membership",
        )
        if name in tables
    ]
    if not candidates:
        return {
            "temporal_membership_table_present": False,
            "selection_contract": (
                "current_asset_cohort_research_not_survivorship_free"
            ),
        }

    detail = {}
    for name in candidates:
        detail[name] = {
            "rows": int(_scalar(conn, f"SELECT COUNT(*) FROM {name}") or 0),
            "columns": _columns(conn, name),
        }

    return {
        "temporal_membership_table_present": True,
        "tables": detail,
        "warning": (
            "Presence of a table is not proof of historically sourced "
            "constituent membership. Inspect membership semantics before "
            "using it as a survivorship-free universe."
        ),
    }


def _recommendation(
    assets: dict[str, Any],
    daily: dict[str, Any],
    panel: dict[str, Any],
    proxies: dict[str, Any],
    macro: dict[str, Any],
) -> dict[str, Any]:
    active_equities = int(assets["active_equities"])
    history_counts = daily["assets_by_minimum_history_days"]
    assets_1260 = int(history_counts.get("1260", 0))
    assets_2000 = int(history_counts.get("2000", 0))
    latest_eligible = int(
        (panel.get("latest") or {}).get("eligible_253", 0)
    )

    missing_core_proxies = [
        ticker
        for ticker in DESIRED_MARKET_PROXIES
        if proxies[ticker]["quality_gated_daily_days"] < 1260
    ]

    if assets_1260 >= 300 and latest_eligible >= 300:
        price_panel_status = "BROAD_PANEL_READY"
        next_price_action = (
            "Build Market Daily V003 panel from existing quality-gated "
            "daily observations; do not backfill equities."
        )
    elif assets_1260 >= 100 and latest_eligible >= 100:
        price_panel_status = "PARTIAL_PANEL_READY"
        next_price_action = (
            "A usable broad panel exists, but inspect historical coverage "
            "concentration before choosing whether to backfill missing assets."
        )
    else:
        price_panel_status = "BROAD_PANEL_BACKFILL_REQUIRED"
        next_price_action = (
            "Backfill quality-gated daily history for the active-equity "
            "research cohort before training Market Daily V003."
        )

    return {
        "price_panel_status": price_panel_status,
        "active_equities": active_equities,
        "assets_with_at_least_1260_days": assets_1260,
        "assets_with_at_least_2000_days": assets_2000,
        "latest_assets_eligible_for_253_day_state": latest_eligible,
        "next_price_action": next_price_action,
        "missing_core_market_proxies_5y": missing_core_proxies,
        "proxy_action": (
            "Ingest broad-market proxies with the same daily causal price "
            "contract if missing. Sector/rate/volatility proxies remain "
            "optional enrichments, not prerequisites for the first panel."
        ),
        "macro_action": (
            "Do not use legacy macro_observations as Market V003 features."
            if not macro["usable_for_market_v003_features_now"]
            else "Causal macro contract appears available; inspect before use."
        ),
        "survivorship_disclosure": (
            "The initial broad panel is a current-asset research cohort, "
            "not a historically constituent-complete universe."
        ),
    }


def audit_database(db: Path) -> dict[str, Any]:
    if not db.is_file():
        raise FileNotFoundError(db)

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        tables = _objects(conn, "table")
        views = _objects(conn, "view")

        required_tables = {
            "assets",
            "price_bar_observations",
            "price_bar_versions",
            "price_quality_runs",
            "price_quality_results",
        }
        missing_tables = sorted(required_tables - tables)
        missing_views = [] if QUALITY_VIEW in views else [QUALITY_VIEW]

        if missing_tables or missing_views:
            return {
                "status": "FAIL",
                "failures": {
                    "missing_tables": missing_tables,
                    "missing_views": missing_views,
                },
                "db": str(db),
            }

        assets = _asset_summary(conn)
        daily = _daily_coverage(conn)
        panel = _eligible_panel_by_day(conn)
        sectors = _sector_readiness_latest(conn)
        proxies = _proxy_coverage(conn)
        legacy = _legacy_price_summary(conn)
        macro = _macro_summary(conn)
        actions = _corporate_action_summary(conn)
        universe = _universe_summary(conn)

        asof_configs = []
        if "daily_price_asof_configs" in tables:
            asof_configs = [
                dict(row)
                for row in _rows(
                    conn,
                    """
                    SELECT
                        asof_contract_version,
                        mode,
                        cutoff_column,
                        required_quality_version,
                        selection_point_in_time_verified,
                        adjusted_close_role,
                        disclosure
                    FROM daily_price_asof_configs
                    ORDER BY mode
                    """,
                )
            ]

        recommendation = _recommendation(
            assets, daily, panel, proxies, macro
        )

        return {
            "status": "PASS",
            "failures": [],
            "db": str(db),
            "contract": contract_dict(),
            "assets": assets,
            "daily_quality_gated": daily,
            "dynamic_panel_readiness": panel,
            "sector_readiness_latest": sectors,
            "proxy_coverage": proxies,
            "daily_asof_configs": asof_configs,
            "corporate_actions": actions,
            "legacy_prices": legacy,
            "macro": macro,
            "universe": universe,
            "recommendation": recommendation,
        }

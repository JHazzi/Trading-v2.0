#!/usr/bin/env python3
"""
Target generator v2.

Temporal semantics:
- intrasession: future bars MUST remain inside origin_session_id.
- overnight: close of a regular session -> next regular-session open.
- max_origins: maximum number of origin observations PER ASSET.
  For intrasession, each selected origin can generate one row per requested horizon.
  Therefore max total rows per asset <= max_origins * len(horizons).
- replace: removes only the selected scope (and selected asset when --asset-id is used)
  before generating new rows.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from pathlib import Path
from typing import Iterable

DB_PATH = Path("data/database/market_data_v2.db")
TARGET_VERSION = "target_v2.1.0"


def connect(db_path: Path = DB_PATH) -> sqlite3.Connection:
    conn = sqlite3.connect(db_path)
    conn.row_factory = sqlite3.Row
    return conn


def parse_horizons(raw: str) -> list[int]:
    values = sorted({int(x.strip()) for x in raw.split(",") if x.strip()})
    if not values or any(v <= 0 for v in values):
        raise ValueError("Los horizontes deben ser enteros positivos.")
    return values


def ensure_required_columns(conn: sqlite3.Connection) -> None:
    cols = {r["name"] for r in conn.execute("PRAGMA table_info(realized_outcomes)")}
    required = {
        "horizon_value",
        "horizon_unit",
        "horizon_scope",
        "origin_session_id",
        "target_session_id",
        "session_type",
        "observed_bars",
        "expected_bars",
        "coverage_pct",
        "max_gap_seconds",
        "session_count",
        "data_quality",
        "quality_version",
        "target_version",
    }
    missing = sorted(required - cols)
    if missing:
        raise RuntimeError(
            "Faltan columnas en realized_outcomes: " + ", ".join(missing)
            + ". Aplicá las migraciones 003-006 antes de usar este generador."
        )


def get_assets(conn: sqlite3.Connection, asset_id: int | None) -> list[int]:
    if asset_id is not None:
        row = conn.execute(
            "SELECT asset_id FROM assets WHERE asset_id = ?", (asset_id,)
        ).fetchone()
        if row is None:
            raise ValueError(f"No existe asset_id={asset_id}")
        return [asset_id]

    return [int(r["asset_id"]) for r in conn.execute(
        "SELECT asset_id FROM assets WHERE active = 1 ORDER BY asset_id"
    )]


def regular_session_bars(conn: sqlite3.Connection, asset_id: int) -> list[sqlite3.Row]:
    return list(
        conn.execute(
            """
            SELECT
                price_bar_id, asset_id, timestamp, open, high, low, close, volume,
                session_id, trading_day
            FROM price_bars
            WHERE asset_id = ?
              AND session_id IS NOT NULL
            ORDER BY timestamp
            """,
            (asset_id,),
        )
    )


def delete_scope(
    conn: sqlite3.Connection, *,
    asset_id: int | None,
    scope: str
) -> None:
    sql = "DELETE FROM realized_outcomes WHERE horizon_scope = ?"
    params: list[object] = [scope]
    if asset_id is not None:
        sql += " AND asset_id = ?"
        params.append(asset_id)
    conn.execute(sql, params)
    conn.commit()


def percent_return(start: float, end: float) -> float:
    if start is None or end is None or start <= 0:
        raise ValueError("Precios inválidos para calcular retorno.")
    return (end / start - 1.0) * 100.0


def outcome_for_path(
    *,
    asset_id: int,
    origin: sqlite3.Row,
    future: list[sqlite3.Row],
    horizon_value: int,
    horizon_unit: str,
    horizon_scope: str,
    target_session_id: str,
    session_type: str,
) -> tuple:
    start = float(origin["close"])
    end = float(future[-1]["close"])

    path_returns = [
        percent_return(start, float(bar["close"]))
        for bar in future
    ]

    mfe = max([0.0, *path_returns])
    mae = min([0.0, *path_returns])
    realized_vol = None

    if len(future) >= 2:
        step_returns = [
            percent_return(float(future[i - 1]["close"]), float(future[i]["close"]))
            for i in range(1, len(future))
        ]
        if step_returns:
            import statistics
            realized_vol = statistics.pstdev(step_returns)

    timestamps = [origin["timestamp"], *(bar["timestamp"] for bar in future)]
    try:
        max_gap_seconds = max(
            0.0,
            *(
                __import__("datetime").datetime.fromisoformat(timestamps[i].replace("Z", "+00:00")).timestamp()
                - __import__("datetime").datetime.fromisoformat(timestamps[i-1].replace("Z", "+00:00")).timestamp()
                for i in range(1, len(timestamps))
            )
        )
    except Exception:
        max_gap_seconds = None

    expected = len(future)
    observed = len(future)
    coverage = 100.0 if expected else None

    path = [
        {"timestamp": origin["timestamp"], "close": start, "return_pct": 0.0},
        *[
            {
                "timestamp": bar["timestamp"],
                "close": float(bar["close"]),
                "return_pct": ret,
            }
            for bar, ret in zip(future, path_returns)
        ],
    ]

    return (
        asset_id,
        origin["timestamp"],
        horizon_value if horizon_unit != "next_open" else 1,
        horizon_unit,
        horizon_scope,
        future[-1]["timestamp"],
        start,
        end,
        percent_return(start, end),
        mfe,
        mae,
        min(float(bar["low"]) for bar in future),
        max(float(bar["high"]) for bar in future),
        realized_vol,
        json.dumps(path, separators=(",", ":")),
        "legacy:price_bars",
        TARGET_VERSION,
        observed,
        expected,
        coverage,
        max_gap_seconds,
        1,
        "good",
        "quality_v1",
        origin["session_id"],
        target_session_id,
        session_type,
    )


def upsert_outcome(conn: sqlite3.Connection, row: tuple) -> None:
    conn.execute(
        """
        INSERT INTO realized_outcomes (
            asset_id, origin_time,
            horizon_seconds,
            horizon_value, horizon_unit, horizon_scope,
            end_time,
            start_price, end_price,
            return_pct, mfe_pct, mae_pct,
            min_price, max_price, realized_volatility, path_json,
            source, target_version,
            observed_bars, expected_bars, coverage_pct,
            max_gap_seconds, session_count, data_quality,
            quality_version, origin_session_id, target_session_id,
            session_type
        )
        VALUES (
            ?, ?, ?,
            ?, ?, ?,
            ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?, ?,
            ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?, ?, ?,
            ?
        )
        ON CONFLICT(asset_id, origin_time, horizon_seconds, target_version)
        DO UPDATE SET
            horizon_value=excluded.horizon_value,
            horizon_unit=excluded.horizon_unit,
            horizon_scope=excluded.horizon_scope,
            end_time=excluded.end_time,
            start_price=excluded.start_price,
            end_price=excluded.end_price,
            return_pct=excluded.return_pct,
            mfe_pct=excluded.mfe_pct,
            mae_pct=excluded.mae_pct,
            min_price=excluded.min_price,
            max_price=excluded.max_price,
            realized_volatility=excluded.realized_volatility,
            path_json=excluded.path_json,
            observed_bars=excluded.observed_bars,
            expected_bars=excluded.expected_bars,
            coverage_pct=excluded.coverage_pct,
            max_gap_seconds=excluded.max_gap_seconds,
            session_count=excluded.session_count,
            data_quality=excluded.data_quality,
            quality_version=excluded.quality_version,
            origin_session_id=excluded.origin_session_id,
            target_session_id=excluded.target_session_id,
            session_type=excluded.session_type
        """,
        (
            row[0], row[1],
            # Compatibility horizon_seconds:
            int(row[2]) * 60 if row[3] == "bar" else 0,
            *row[2:],
        ),
    )


def generate_intrasession(
    conn: sqlite3.Connection,
    asset_id: int,
    horizons: list[int],
    max_origins: int | None,
) -> int:
    bars = regular_session_bars(conn, asset_id)

    # Group by session while preserving chronological order.
    sessions: dict[str, list[sqlite3.Row]] = {}
    for bar in bars:
        sessions.setdefault(str(bar["session_id"]), []).append(bar)

    inserted = 0

    for session_id, session_bars in sessions.items():
        # Every origin is a market observation. max_origins is per asset,
        # so we don't slice each session independently.
        pass

    # Build a single chronological list of valid origins, then cap it PER ASSET.
    valid_origins: list[tuple[sqlite3.Row, list[sqlite3.Row]]] = []
    for session_id, session_bars in sessions.items():
        for idx, origin in enumerate(session_bars):
            for horizon in horizons:
                if idx + horizon < len(session_bars):
                    valid_origins.append((origin, session_bars[idx + 1 : idx + horizon + 1]))
                    break  # origin only needs to be considered once here.

    valid_origins.sort(key=lambda x: x[0]["timestamp"])
    if max_origins is not None:
        valid_origins = valid_origins[:max_origins]

    origin_to_horizons: dict[str, sqlite3.Row] = {
        origin["timestamp"]: origin for origin, _ in valid_origins
    }

    for origin, _ in valid_origins:
        # IMPORTANT: once an origin is selected, generate ALL requested horizons
        # that fit inside the same session.
        session_bars = sessions[str(origin["session_id"])]
        idx = next(i for i, b in enumerate(session_bars) if b["timestamp"] == origin["timestamp"])

        for horizon in horizons:
            end_idx = idx + horizon
            if end_idx >= len(session_bars):
                continue

            future = session_bars[idx + 1 : end_idx + 1]
            row = outcome_for_path(
                asset_id=asset_id,
                origin=origin,
                future=future,
                horizon_value=horizon,
                horizon_unit="bar",
                horizon_scope="intrasession",
                target_session_id=str(origin["session_id"]),
                session_type="regular",
            )
            upsert_outcome(conn, row)
            inserted += 1

    conn.commit()
    return inserted


def generate_overnight(
    conn: sqlite3.Connection,
    asset_id: int,
    max_origins: int | None,
) -> int:
    bars = regular_session_bars(conn, asset_id)

    sessions: list[tuple[str, list[sqlite3.Row]]] = []
    by_session: dict[str, list[sqlite3.Row]] = {}
    for bar in bars:
        by_session.setdefault(str(bar["session_id"]), []).append(bar)

    for session_id, session_bars in sorted(
        by_session.items(),
        key=lambda kv: kv[1][0]["timestamp"]
    ):
        sessions.append((session_id, session_bars))

    pairs: list[tuple[sqlite3.Row, sqlite3.Row, str]] = []
    for i in range(len(sessions) - 1):
        sid, current = sessions[i]
        next_sid, next_session = sessions[i + 1]
        origin = current[-1]
        target = next_session[0]
        pairs.append((origin, target, next_sid))

    if max_origins is not None:
        pairs = pairs[:max_origins]

    inserted = 0
    for origin, target, target_sid in pairs:
        start = float(origin["close"])
        end = float(target["open"])
        ret = percent_return(start, end)

        path = [
            {"timestamp": origin["timestamp"], "close": start, "return_pct": 0.0},
            {"timestamp": target["timestamp"], "close": end, "return_pct": ret},
        ]

        row = (
            asset_id,
            origin["timestamp"],
            0,  # compatibility horizon_seconds for next_open
            1,
            "session",
            "next_open",
            target["timestamp"],
            start,
            end,
            ret,
            max(0.0, ret),
            min(0.0, ret),
            float(target["low"]),
            float(target["high"]),
            None,
            json.dumps(path, separators=(",", ":")),
            "legacy:price_bars",
            TARGET_VERSION,
            1,
            1,
            100.0,
            None,
            1,
            "good",
            "quality_v1",
            origin["session_id"],
            target_sid,
            "regular",
        )
        upsert_outcome(conn, row)
        inserted += 1

    conn.commit()
    return inserted


def generate(
    asset_id: int | None,
    scope: str,
    horizons: list[int],
    max_origins: int | None,
    replace: bool,
) -> int:
    conn = connect()
    try:
        ensure_required_columns(conn)
        assets = get_assets(conn, asset_id)

        if replace:
            delete_scope(conn, asset_id=asset_id, scope=scope)

        total = 0
        for aid in assets:
            if scope == "intrasession":
                count = generate_intrasession(conn, aid, horizons, max_origins)
            elif scope == "overnight":
                count = generate_overnight(conn, aid, max_origins)
            else:
                raise ValueError("scope debe ser 'intrasession' u 'overnight'.")

            print(f"Asset {aid} procesado: {count} outcomes.")
            total += count

        return total
    finally:
        conn.close()


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--asset-id", type=int)
    parser.add_argument("--scope", choices=["intrasession", "overnight"], default="intrasession")
    parser.add_argument("--max-origins", type=int)
    parser.add_argument("--horizons", default="5,15,30,60")
    parser.add_argument("--replace", action="store_true")
    args = parser.parse_args()

    horizons = parse_horizons(args.horizons)

    if args.max_origins is not None and args.max_origins <= 0:
        raise SystemExit("--max-origins debe ser > 0.")

    if args.scope == "overnight":
        horizons = [1]

    total = generate(
        asset_id=args.asset_id,
        scope=args.scope,
        horizons=horizons,
        max_origins=args.max_origins,
        replace=args.replace,
    )
    print(f"Completado. Outcomes upserted: {total}")


if __name__ == "__main__":
    main()

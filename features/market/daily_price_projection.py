from __future__ import annotations

import argparse
import json
import sqlite3
from dataclasses import asdict, dataclass
from pathlib import Path
from typing import Literal

ProjectionMode = Literal["target_final", "research_asof", "strict_pit"]

REQUIRED_TABLES = {
    "assets",
    "price_bar_versions",
    "price_bar_observations",
    "price_quality_runs",
    "price_quality_results",
}


@dataclass(frozen=True)
class DailyPrice:
    asset_id: int
    trading_day: str
    price_bar_version_id: str
    price_observation_id: str
    source_id: str
    open: float | None
    high: float | None
    low: float | None
    close: float | None
    volume: float | None
    adjusted_close: float | None
    bar_start_utc: str
    bar_end_utc: str
    observed_at: str
    available_at: str
    point_in_time_verified: bool
    observation_kind: str
    observation_sequence: int


def _tables(conn: sqlite3.Connection) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }


def validate_contract(conn: sqlite3.Connection) -> None:
    missing = sorted(REQUIRED_TABLES - _tables(conn))
    if missing:
        raise RuntimeError(f"Faltan tablas de precio diario: {missing}")


def resolve_asset_id(
    conn: sqlite3.Connection,
    *,
    asset_id: int | None = None,
    ticker: str | None = None,
) -> int:
    if (asset_id is None) == (ticker is None):
        raise ValueError("Indicá exactamente uno de asset_id o ticker")

    if asset_id is not None:
        row = conn.execute(
            "SELECT asset_id FROM assets WHERE asset_id = ?",
            (asset_id,),
        ).fetchone()
    else:
        row = conn.execute(
            "SELECT asset_id FROM assets WHERE ticker = ? COLLATE NOCASE",
            (ticker,),
        ).fetchone()

    if row is None:
        raise KeyError(f"Activo inexistente: asset_id={asset_id}, ticker={ticker}")
    return int(row[0])


def load_daily_projection(
    conn: sqlite3.Connection,
    *,
    asset_id: int,
    start_day: str,
    end_day: str,
    mode: ProjectionMode,
    as_of: str | None = None,
) -> list[DailyPrice]:
    """
    Read-only projection over append-only observations.

    Modes
    -----
    target_final:
        Latest quality-approved observation for each trading day.
        Appropriate for realized outcome / reaction labels. It is NOT a
        reconstruction of what was known historically.

    research_asof:
        Latest quality-approved observation whose available_at <= as_of.
        Allows controlled research backfill assumptions (PIT flag may be 0).

    strict_pit:
        Same as research_asof, but only point_in_time_verified = 1.
        Appropriate when claiming exact system replay.
    """
    validate_contract(conn)

    if mode not in {"target_final", "research_asof", "strict_pit"}:
        raise ValueError(f"Projection mode inválido: {mode!r}")

    if mode != "target_final" and not as_of:
        raise ValueError(f"mode={mode} requiere as_of")
    if mode == "target_final" and as_of is not None:
        raise ValueError("target_final no acepta as_of")

    time_filter = ""
    params: list[object] = [asset_id, start_day, end_day]

    if mode in {"research_asof", "strict_pit"}:
        # julianday correctly compares timezone offsets; raw TEXT comparison does not.
        time_filter += " AND julianday(o.available_at) <= julianday(?)"
        params.append(as_of)

    if mode == "strict_pit":
        time_filter += " AND o.point_in_time_verified = 1"

    sql = f"""
    WITH candidates AS (
        SELECT
            o.price_observation_id,
            o.price_bar_version_id,
            o.source_id,
            o.asset_id,
            o.trading_day,
            o.observed_at,
            o.available_at,
            o.point_in_time_verified,
            o.observation_kind,
            o.observation_sequence,
            v.open,
            v.high,
            v.low,
            v.close,
            v.volume,
            v.adjusted_close,
            v.bar_start_utc,
            v.bar_end_utc,
            ROW_NUMBER() OVER (
                PARTITION BY o.asset_id, o.trading_day
                ORDER BY
                    o.observation_sequence DESC,
                    julianday(o.observed_at) DESC,
                    o.price_observation_id DESC
            ) AS rn
        FROM price_bar_observations AS o
        JOIN price_bar_versions AS v
          ON v.price_bar_version_id = o.price_bar_version_id
        WHERE o.asset_id = ?
          AND o.trading_day >= ?
          AND o.trading_day <= ?
          {time_filter}
          AND EXISTS (
              SELECT 1
              FROM price_quality_runs AS qr
              WHERE qr.batch_retrieval_id = o.batch_retrieval_id
                AND qr.status = 'completed'
                AND NOT EXISTS (
                    SELECT 1
                    FROM price_quality_results AS qres
                    WHERE qres.quality_run_id = qr.quality_run_id
                      AND qres.asset_id = o.asset_id
                      AND qres.check_status = 'fail'
                )
          )
    )
    SELECT
        asset_id,
        trading_day,
        price_bar_version_id,
        price_observation_id,
        source_id,
        open,
        high,
        low,
        close,
        volume,
        adjusted_close,
        bar_start_utc,
        bar_end_utc,
        observed_at,
        available_at,
        point_in_time_verified,
        observation_kind,
        observation_sequence
    FROM candidates
    WHERE rn = 1
    ORDER BY trading_day
    """

    rows = conn.execute(sql, params).fetchall()
    return [
        DailyPrice(
            asset_id=int(r[0]),
            trading_day=str(r[1]),
            price_bar_version_id=str(r[2]),
            price_observation_id=str(r[3]),
            source_id=str(r[4]),
            open=r[5],
            high=r[6],
            low=r[7],
            close=r[8],
            volume=r[9],
            adjusted_close=r[10],
            bar_start_utc=str(r[11]),
            bar_end_utc=str(r[12]),
            observed_at=str(r[13]),
            available_at=str(r[14]),
            point_in_time_verified=bool(r[15]),
            observation_kind=str(r[16]),
            observation_sequence=int(r[17]),
        )
        for r in rows
    ]


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument(
        "--db",
        default="data/database/market_data_v2.db",
        type=Path,
    )
    group = ap.add_mutually_exclusive_group(required=True)
    group.add_argument("--asset-id", type=int)
    group.add_argument("--ticker")
    ap.add_argument("--start", required=True)
    ap.add_argument("--end", required=True)
    ap.add_argument(
        "--mode",
        choices=("target_final", "research_asof", "strict_pit"),
        required=True,
    )
    ap.add_argument("--as-of")
    ap.add_argument("--limit", type=int, default=10)
    args = ap.parse_args()

    if not args.db.is_file():
        raise SystemExit(f"DB inexistente: {args.db}")

    with sqlite3.connect(args.db) as conn:
        aid = resolve_asset_id(
            conn,
            asset_id=args.asset_id,
            ticker=args.ticker,
        )
        rows = load_daily_projection(
            conn,
            asset_id=aid,
            start_day=args.start,
            end_day=args.end,
            mode=args.mode,
            as_of=args.as_of,
        )

    payload = {
        "asset_id": aid,
        "mode": args.mode,
        "rows": len(rows),
        "pit_verified_rows": sum(x.point_in_time_verified for x in rows),
        "first_day": rows[0].trading_day if rows else None,
        "last_day": rows[-1].trading_day if rows else None,
        "sample": [asdict(x) for x in rows[: max(0, args.limit)]],
    }
    print(json.dumps(payload, indent=2, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

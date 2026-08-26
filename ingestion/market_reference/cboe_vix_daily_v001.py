from __future__ import annotations

import argparse
import csv
import hashlib
import io
import json
import os
import sqlite3
import urllib.request
import uuid
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = (
    ROOT / "config" / "market_brain_daily_v0052_financial_conditions.json"
)


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: object) -> str:
    payload = "\0".join(str(x) for x in parts).encode("utf-8")
    return prefix + "_" + hashlib.sha256(payload).hexdigest()


def schema(conn: sqlite3.Connection):
    conn.executescript("""
    CREATE TABLE IF NOT EXISTS global_reference_sources(
      source_id TEXT PRIMARY KEY,
      description TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS raw_global_reference_batches(
      raw_batch_id TEXT PRIMARY KEY,
      source_id TEXT NOT NULL,
      symbol TEXT NOT NULL,
      source_url TEXT NOT NULL,
      retrieved_at TEXT NOT NULL,
      raw_sha256 TEXT NOT NULL,
      storage_path TEXT NOT NULL,
      byte_length INTEGER NOT NULL,
      row_count INTEGER NOT NULL,
      parser_version TEXT NOT NULL
    );

    CREATE TABLE IF NOT EXISTS global_reference_versions(
      version_id TEXT PRIMARY KEY,
      source_id TEXT NOT NULL,
      symbol TEXT NOT NULL,
      trading_day TEXT NOT NULL,
      open REAL NOT NULL,
      high REAL NOT NULL,
      low REAL NOT NULL,
      close REAL NOT NULL,
      content_sha256 TEXT NOT NULL,
      normalized_json TEXT NOT NULL,
      first_raw_batch_id TEXT NOT NULL,
      UNIQUE(source_id,symbol,trading_day,content_sha256)
    );

    CREATE TABLE IF NOT EXISTS global_reference_observations(
      observation_id TEXT PRIMARY KEY,
      version_id TEXT NOT NULL,
      raw_batch_id TEXT NOT NULL,
      source_id TEXT NOT NULL,
      symbol TEXT NOT NULL,
      trading_day TEXT NOT NULL,
      observed_at TEXT NOT NULL,
      available_at TEXT NOT NULL,
      availability_basis TEXT NOT NULL,
      point_in_time_verified INTEGER NOT NULL CHECK(
        point_in_time_verified IN (0,1)
      ),
      observation_kind TEXT NOT NULL,
      observation_sequence INTEGER NOT NULL,
      previous_observation_id TEXT,
      UNIQUE(source_id,symbol,trading_day,observation_sequence)
    );

    CREATE INDEX IF NOT EXISTS idx_gro_symbol_day
      ON global_reference_observations(source_id,symbol,trading_day);
    """)


def fetch_bytes(url: str, timeout: int = 30) -> bytes:
    req = urllib.request.Request(
        url,
        headers={"User-Agent": "quant-market-ai-research/1.0"},
    )
    with urllib.request.urlopen(req, timeout=timeout) as response:
        return response.read()


def _pick(row: dict[str, str], *names: str) -> str:
    lowered = {str(k).strip().lower(): v for k, v in row.items()}
    for name in names:
        key = name.lower()
        if key in lowered:
            return str(lowered[key]).strip()
    raise ValueError(f"missing CSV field among {names}")


def _date_value(text: str) -> str:
    text = text.strip()
    for fmt in ("%m/%d/%Y", "%Y-%m-%d", "%m/%d/%y"):
        try:
            return datetime.strptime(text, fmt).date().isoformat()
        except ValueError:
            pass
    raise ValueError(f"unsupported VIX date: {text!r}")


def parse_csv(
    raw: bytes,
    start: str | None = None,
    end_exclusive: str | None = None,
) -> list[dict]:
    """
    Parse and validate only the requested research window.

    Cboe's 1990-present file contains legacy history that is irrelevant to
    V005.2. An anomalous pre-window row must not block a 2016+ research
    dataset. Quality gates remain strict for every row inside the requested
    window.

    When no bounds are supplied, behavior remains strict over the full file.
    """
    if start is not None:
        date.fromisoformat(start)
    if end_exclusive is not None:
        date.fromisoformat(end_exclusive)
    if (
        start is not None
        and end_exclusive is not None
        and start >= end_exclusive
    ):
        raise ValueError("start must be before end_exclusive")

    text = raw.decode("utf-8-sig")
    rows = []
    seen = set()

    for row in csv.DictReader(io.StringIO(text)):
        # Date is the only field required to decide whether this row belongs
        # to the research contract. Do not parse/validate out-of-window OHLC.
        day = _date_value(_pick(row, "DATE", "Date"))

        if start is not None and day < start:
            continue
        if end_exclusive is not None and day >= end_exclusive:
            continue

        values = {
            "open": float(_pick(row, "OPEN", "Open")),
            "high": float(_pick(row, "HIGH", "High")),
            "low": float(_pick(row, "LOW", "Low")),
            "close": float(_pick(row, "CLOSE", "Close")),
        }

        if day in seen:
            raise ValueError(f"duplicate VIX trading day: {day}")
        seen.add(day)

        if values["high"] < max(
            values["open"], values["low"], values["close"]
        ):
            raise ValueError(f"invalid VIX high on {day}")
        if values["low"] > min(
            values["open"], values["high"], values["close"]
        ):
            raise ValueError(f"invalid VIX low on {day}")
        if any(not (value >= 0.0) for value in values.values()):
            raise ValueError(f"invalid negative/non-finite VIX value on {day}")

        rows.append({"trading_day": day, **values})

    if not rows:
        scope = (
            "full file"
            if start is None and end_exclusive is None
            else f"[{start or '-inf'}, {end_exclusive or '+inf'})"
        )
        raise ValueError(f"empty Cboe VIX CSV for scope {scope}")

    rows.sort(key=lambda x: x["trading_day"])
    return rows


def equity_session_closes(
    start: str,
    end_exclusive: str,
) -> dict[str, datetime]:
    import exchange_calendars

    start_date = date.fromisoformat(start)
    end_date = date.fromisoformat(end_exclusive)
    cal = exchange_calendars.get_calendar(
        "XNYS",
        start=start_date - timedelta(days=7),
        end=end_date + timedelta(days=7),
    )
    out: dict[str, datetime] = {}
    for session in cal.sessions:
        day = session.date().isoformat()
        if not (start <= day < end_exclusive):
            continue
        close = cal.session_close(session).to_pydatetime()
        if close.tzinfo is None:
            close = close.replace(tzinfo=timezone.utc)
        out[day] = close.astimezone(timezone.utc)
    return out


def vix_availability(
    day: str,
    session_closes: dict[str, datetime],
    retrieved_at: str,
    normal_basis: str,
) -> tuple[str, str, bool]:
    """
    Preserve provider dates outside XNYS for lineage, but do not assign them
    the equity-origin clock and do not make them model eligible.
    """
    close = session_closes.get(day)
    if close is None:
        return (
            retrieved_at,
            "provider_non_equity_session_retrieval_only_not_model_eligible",
            False,
        )
    return (
        (close + timedelta(minutes=15)).isoformat(),
        normal_basis,
        True,
    )

def write_raw(raw_root: Path, raw: bytes) -> tuple[Path, str]:
    sha = hashlib.sha256(raw).hexdigest()
    dest = (
        raw_root
        / "market_reference"
        / "cboe"
        / "vix"
        / sha[:2]
        / f"{sha}.csv"
    )
    dest.parent.mkdir(parents=True, exist_ok=True)
    if not dest.exists():
        tmp = dest.with_suffix(dest.suffix + f".{uuid.uuid4().hex}.tmp")
        tmp.write_bytes(raw)
        try:
            os.link(tmp, dest)
        except FileExistsError:
            pass
        finally:
            tmp.unlink(missing_ok=True)
    if hashlib.sha256(dest.read_bytes()).hexdigest() != sha:
        raise RuntimeError("Cboe VIX raw hash mismatch")
    return dest, sha


def acquire(config_path: Path = DEFAULT_CONFIG) -> dict:
    cfg = json.loads(config_path.read_text(encoding="utf-8"))
    spec = cfg["vix_source"]
    db = ROOT / cfg["vix_db"]
    raw_root = ROOT / "data" / "raw"
    start = cfg["date_window"]["start"]
    end = cfg["date_window"]["end_exclusive"]

    raw = fetch_bytes(spec["url"])

    # IMPORTANT: window filtering now occurs inside parse_csv, before OHLC
    # validation. This prevents irrelevant legacy rows outside the research
    # window from blocking acquisition, while preserving strict validation for
    # every row actually used by V005.2.
    rows = parse_csv(
        raw,
        start=start,
        end_exclusive=end,
    )

    path, sha = write_raw(raw_root, raw)
    retrieved = utc_now()
    session_closes = equity_session_closes(start, end)
    batch_id = stable_id(
        "grb", spec["source_id"], spec["symbol"], sha
    )

    db.parent.mkdir(parents=True, exist_ok=True)
    inserted_versions = 0
    inserted_observations = 0
    equity_session_rows = 0
    non_equity_session_rows = 0

    with sqlite3.connect(db) as conn:
        schema(conn)
        conn.execute(
            "INSERT OR IGNORE INTO global_reference_sources VALUES (?,?)",
            (
                spec["source_id"],
                "Cboe VIX daily historical index values",
            ),
        )
        conn.execute(
            """
            INSERT OR IGNORE INTO raw_global_reference_batches(
              raw_batch_id,source_id,symbol,source_url,retrieved_at,
              raw_sha256,storage_path,byte_length,row_count,parser_version
            ) VALUES (?,?,?,?,?,?,?,?,?,?)
            """,
            (
                batch_id,
                spec["source_id"],
                spec["symbol"],
                spec["url"],
                retrieved,
                sha,
                str(path),
                len(raw),
                len(rows),
                spec["parser_version"],
            ),
        )

        for row in rows:
            normalized = json.dumps(
                row,
                sort_keys=True,
                separators=(",", ":"),
            )
            content_sha = hashlib.sha256(
                normalized.encode("utf-8")
            ).hexdigest()
            version_id = stable_id(
                "grv",
                spec["source_id"],
                spec["symbol"],
                row["trading_day"],
                content_sha,
            )
            inserted_versions += conn.execute(
                """
                INSERT OR IGNORE INTO global_reference_versions(
                  version_id,source_id,symbol,trading_day,open,high,low,close,
                  content_sha256,normalized_json,first_raw_batch_id
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    version_id,
                    spec["source_id"],
                    spec["symbol"],
                    row["trading_day"],
                    row["open"],
                    row["high"],
                    row["low"],
                    row["close"],
                    content_sha,
                    normalized,
                    batch_id,
                ),
            ).rowcount

            previous = conn.execute(
                """
                SELECT observation_id,version_id,observation_sequence
                FROM global_reference_observations
                WHERE source_id=? AND symbol=? AND trading_day=?
                ORDER BY observation_sequence DESC LIMIT 1
                """,
                (
                    spec["source_id"],
                    spec["symbol"],
                    row["trading_day"],
                ),
            ).fetchone()

            if previous is None:
                seq = 1
                previous_id = None
                available_at, basis, model_eligible = vix_availability(
                    row["trading_day"],
                    session_closes,
                    retrieved,
                    spec["initial_availability_basis"],
                )
                if model_eligible:
                    kind = "initial_backfill"
                    equity_session_rows += 1
                else:
                    kind = "initial_backfill_non_equity_session"
                    non_equity_session_rows += 1
            else:
                seq = int(previous[2]) + 1
                previous_id = str(previous[0])
                kind = (
                    "unchanged"
                    if str(previous[1]) == version_id
                    else "revision"
                )
                available_at = retrieved
                basis = "retrieval_time_revision_or_confirmation"

            obs_id = stable_id(
                "gro",
                batch_id,
                row["trading_day"],
                seq,
                retrieved,
            )
            inserted_observations += conn.execute(
                """
                INSERT OR IGNORE INTO global_reference_observations(
                  observation_id,version_id,raw_batch_id,source_id,symbol,
                  trading_day,observed_at,available_at,availability_basis,
                  point_in_time_verified,observation_kind,
                  observation_sequence,previous_observation_id
                ) VALUES (?,?,?,?,?,?,?,?,?,0,?,?,?)
                """,
                (
                    obs_id,
                    version_id,
                    batch_id,
                    spec["source_id"],
                    spec["symbol"],
                    row["trading_day"],
                    retrieved,
                    available_at,
                    basis,
                    kind,
                    seq,
                    previous_id,
                ),
            ).rowcount

        conn.commit()

    return {
        "status": "PASS",
        "source_id": spec["source_id"],
        "symbol": spec["symbol"],
        "raw_sha256": sha,
        "window_rows": len(rows),
        "first_day": rows[0]["trading_day"],
        "last_day": rows[-1]["trading_day"],
        "versions_inserted": inserted_versions,
        "observations_inserted": inserted_observations,
        "strict_historical_pit": False,
        "parser_window_filter_before_ohlc_validation": True,
        "equity_session_rows": equity_session_rows,
        "non_equity_session_rows": non_equity_session_rows,
        "non_equity_session_policy": (
            "preserve_provider_row_but_exclude_from_model_state"
        ),
    }


if __name__ == "__main__":
    p = argparse.ArgumentParser()
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    a = p.parse_args()
    print(json.dumps(acquire(a.config), indent=2))

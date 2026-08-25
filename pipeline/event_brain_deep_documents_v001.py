from __future__ import annotations

import argparse
import json
import os
import sqlite3
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
COHORT_CONFIG = ROOT / "config" / "event_brain_deep_history_v001.json"
DOC_CONFIG = ROOT / "config" / "event_brain_deep_documents_v001.json"


@dataclass(frozen=True)
class PriceWindow:
    asset_id: int
    ticker: str
    ready_day: str
    last_day: str
    distinct_price_days: int


@dataclass(frozen=True)
class EligibleFiling:
    ticker: str
    asset_id: int
    filing_raw_document_id: str
    accession_number: str
    form: str
    acceptance_datetime: str
    acceptance_day: str
    cik: str
    has_downloaded_content: bool


def load_json(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def configured_tickers() -> list[str]:
    cfg = load_json(COHORT_CONFIG)
    tickers = {
        str(t).upper()
        for t in cfg["automatic_tickers"]
    }
    tickers.update(
        str(x["ticker"]).upper()
        for x in cfg["explicit_issuer_targets"]
    )
    return sorted(tickers)


def configured_forms() -> list[str]:
    cfg = load_json(COHORT_CONFIG)
    return [str(x).upper() for x in cfg["sec_forms"]]


def ensure_contract(conn: sqlite3.Connection) -> None:
    required_tables = {
        "assets",
        "sec_filing_metadata_versions",
        "sec_filing_metadata_observations",
        "sec_filing_files",
        "sec_filing_document_metadata_selections",
    }
    present_tables = {
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='table'"
        )
    }
    missing = sorted(required_tables - present_tables)
    if missing:
        raise RuntimeError(f"Faltan tablas: {missing}")

    views = {
        str(r[0])
        for r in conn.execute(
            "SELECT name FROM sqlite_master WHERE type='view'"
        )
    }
    if "daily_price_quality_gated_observations_v001" not in views:
        # Unit tests may materialize this as a table. Production must have either.
        if "daily_price_quality_gated_observations_v001" not in present_tables:
            raise RuntimeError(
                "Falta daily_price_quality_gated_observations_v001"
            )


def price_windows(
    conn: sqlite3.Connection,
    *,
    warmup_sessions: int,
) -> dict[str, PriceWindow]:
    if warmup_sessions < 2:
        raise ValueError("warmup_sessions debe ser >=2")

    targets = configured_tickers()
    placeholders = ",".join("?" for _ in targets)

    rows = conn.execute(
        f"""
        WITH target_assets AS (
            SELECT asset_id, UPPER(ticker) AS ticker
            FROM assets
            WHERE UPPER(ticker) IN ({placeholders})
        ),
        distinct_days AS (
            SELECT DISTINCT
                g.asset_id,
                g.trading_day
            FROM daily_price_quality_gated_observations_v001 AS g
            JOIN target_assets AS a
              ON a.asset_id=g.asset_id
        ),
        ranked AS (
            SELECT
                d.asset_id,
                a.ticker,
                d.trading_day,
                ROW_NUMBER() OVER (
                    PARTITION BY d.asset_id
                    ORDER BY d.trading_day
                ) AS seq,
                COUNT(*) OVER (
                    PARTITION BY d.asset_id
                ) AS n_days
            FROM distinct_days AS d
            JOIN target_assets AS a
              ON a.asset_id=d.asset_id
        )
        SELECT
            asset_id,
            ticker,
            MAX(CASE WHEN seq=? THEN trading_day END) AS ready_day,
            MAX(trading_day) AS last_day,
            MAX(n_days) AS n_days
        FROM ranked
        GROUP BY asset_id, ticker
        ORDER BY ticker
        """,
        [*targets, warmup_sessions],
    ).fetchall()

    result: dict[str, PriceWindow] = {}
    for asset_id, ticker, ready_day, last_day, n_days in rows:
        if ready_day is None:
            continue
        result[str(ticker).upper()] = PriceWindow(
            asset_id=int(asset_id),
            ticker=str(ticker).upper(),
            ready_day=str(ready_day),
            last_day=str(last_day),
            distinct_price_days=int(n_days),
        )
    return result


def eligible_filings(
    conn: sqlite3.Connection,
    *,
    warmup_sessions: int,
) -> list[EligibleFiling]:
    windows = price_windows(
        conn,
        warmup_sessions=warmup_sessions,
    )
    targets = configured_tickers()
    forms = configured_forms()

    missing_windows = sorted(set(targets) - set(windows))
    if missing_windows:
        raise RuntimeError(
            f"Tickers sin ventana diaria model-ready: {missing_windows}"
        )

    ticker_placeholders = ",".join("?" for _ in targets)
    form_placeholders = ",".join("?" for _ in forms)

    rows = conn.execute(
        f"""
        WITH latest_obs AS (
            SELECT
                o.filing_raw_document_id,
                o.metadata_version_id,
                o.observation_sequence,
                ROW_NUMBER() OVER (
                    PARTITION BY o.filing_raw_document_id
                    ORDER BY o.observation_sequence DESC,
                             julianday(o.available_at) DESC,
                             o.metadata_observation_id DESC
                ) AS rn
            FROM sec_filing_metadata_observations AS o
        ),
        latest_meta AS (
            SELECT
                v.filing_raw_document_id,
                v.accession_number,
                UPPER(v.ticker_at_ingestion) AS ticker,
                v.form,
                v.acceptance_datetime,
                v.cik
            FROM latest_obs AS o
            JOIN sec_filing_metadata_versions AS v
              ON v.metadata_version_id=o.metadata_version_id
             AND v.filing_raw_document_id=o.filing_raw_document_id
            WHERE o.rn=1
              AND UPPER(v.ticker_at_ingestion)
                  IN ({ticker_placeholders})
              AND UPPER(v.form)
                  IN ({form_placeholders})
        )
        SELECT
            m.ticker,
            a.asset_id,
            m.filing_raw_document_id,
            m.accession_number,
            UPPER(m.form) AS form,
            m.acceptance_datetime,
            substr(m.acceptance_datetime,1,10) AS acceptance_day,
            m.cik,
            CASE WHEN EXISTS (
                SELECT 1
                FROM sec_filing_files AS ff
                WHERE ff.filing_raw_document_id=m.filing_raw_document_id
                  AND ff.raw_document_id IS NOT NULL
            ) THEN 1 ELSE 0 END AS has_downloaded_content
        FROM latest_meta AS m
        JOIN assets AS a
          ON UPPER(a.ticker)=m.ticker
        ORDER BY m.acceptance_datetime, m.ticker, m.accession_number
        """,
        [*targets, *forms],
    ).fetchall()

    result: list[EligibleFiling] = []
    for row in rows:
        ticker = str(row[0]).upper()
        window = windows[ticker]
        acceptance_day = str(row[6])
        if acceptance_day < window.ready_day:
            continue
        if acceptance_day > window.last_day:
            continue
        result.append(
            EligibleFiling(
                ticker=ticker,
                asset_id=int(row[1]),
                filing_raw_document_id=str(row[2]),
                accession_number=str(row[3]),
                form=str(row[4]),
                acceptance_datetime=str(row[5]),
                acceptance_day=acceptance_day,
                cik=str(row[7]),
                has_downloaded_content=bool(row[8]),
            )
        )
    return result


def audit(db: Path) -> dict[str, object]:
    dcfg = load_json(DOC_CONFIG)
    warmup = int(dcfg["warmup_sessions"])

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        ensure_contract(conn)
        windows = price_windows(conn, warmup_sessions=warmup)
        filings = eligible_filings(conn, warmup_sessions=warmup)

        by_ticker: dict[str, dict[str, object]] = {}
        by_year: dict[str, dict[str, dict[str, int]]] = {}

        for ticker in configured_tickers():
            window = windows.get(ticker)
            by_ticker[ticker] = {
                "price_ready_day": None if window is None else window.ready_day,
                "last_price_day": None if window is None else window.last_day,
                "price_days": None if window is None else window.distinct_price_days,
                "eligible_filings": 0,
                "already_downloaded_filings": 0,
                "pending_filings": 0,
                "downloaded_raw_documents": 0,
            }

        for f in filings:
            item = by_ticker[f.ticker]
            item["eligible_filings"] += 1
            if f.has_downloaded_content:
                item["already_downloaded_filings"] += 1
            else:
                item["pending_filings"] += 1

            year = f.acceptance_day[:4]
            y = by_year.setdefault(f.ticker, {}).setdefault(
                year,
                {"eligible": 0, "downloaded": 0, "pending": 0},
            )
            y["eligible"] += 1
            if f.has_downloaded_content:
                y["downloaded"] += 1
            else:
                y["pending"] += 1

        # Count only raw documents belonging to eligible filings.
        eligible_ids = [f.filing_raw_document_id for f in filings]
        if eligible_ids:
            for offset in range(0, len(eligible_ids), 800):
                chunk = eligible_ids[offset:offset + 800]
                placeholders = ",".join("?" for _ in chunk)
                raw_rows = conn.execute(
                    f"""
                    SELECT
                        UPPER(v.ticker_at_ingestion) AS ticker,
                        COUNT(DISTINCT ff.raw_document_id)
                    FROM sec_filing_files AS ff
                    JOIN sec_filing_metadata_versions AS v
                      ON v.filing_raw_document_id=ff.filing_raw_document_id
                    WHERE ff.filing_raw_document_id IN ({placeholders})
                      AND ff.raw_document_id IS NOT NULL
                    GROUP BY UPPER(v.ticker_at_ingestion)
                    """,
                    chunk,
                ).fetchall()
                for ticker, count in raw_rows:
                    if str(ticker).upper() in by_ticker:
                        by_ticker[str(ticker).upper()][
                            "downloaded_raw_documents"
                        ] += int(count)

    total_eligible = sum(
        int(x["eligible_filings"]) for x in by_ticker.values()
    )
    total_downloaded = sum(
        int(x["already_downloaded_filings"]) for x in by_ticker.values()
    )
    total_pending = sum(
        int(x["pending_filings"]) for x in by_ticker.values()
    )

    failures = []
    for ticker, item in by_ticker.items():
        if item["price_ready_day"] is None:
            failures.append(f"{ticker}:no_price_window")
        if int(item["eligible_filings"]) < 40:
            failures.append(f"{ticker}:eligible_filings<40")

    return {
        "status": "PASS" if not failures else "REVIEW",
        "failures": failures,
        "selection_contract": dcfg["selection_contract"],
        "warmup_sessions": warmup,
        "configured_tickers": configured_tickers(),
        "configured_forms": configured_forms(),
        "totals": {
            "eligible_filings": total_eligible,
            "already_downloaded_filings": total_downloaded,
            "pending_filings": total_pending,
        },
        "by_ticker": by_ticker,
        "by_year": by_year,
        "documents_complete": total_pending == 0,
    }


def pending_filings(
    db: Path,
    *,
    ticker: str | None = None,
) -> list[EligibleFiling]:
    dcfg = load_json(DOC_CONFIG)
    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        ensure_contract(conn)
        rows = eligible_filings(
            conn,
            warmup_sessions=int(dcfg["warmup_sessions"]),
        )
    pending = [x for x in rows if not x.has_downloaded_content]
    if ticker:
        wanted = ticker.upper()
        pending = [x for x in pending if x.ticker == wanted]
    # Deliberately oldest first: if interrupted, historical regime depth grows
    # before duplicating the already-rich 2024-2026 region.
    return sorted(
        pending,
        key=lambda x: (x.acceptance_datetime, x.ticker, x.accession_number),
    )


def run_documents(
    db: Path,
    *,
    ticker: str | None,
    max_batches: int | None,
    batch_size_override: int | None,
) -> dict[str, object]:
    if not os.environ.get("SEC_USER_AGENT"):
        raise RuntimeError("Falta SEC_USER_AGENT")

    from ingestion.events.sec_filing_documents_v2 import (
        DEFAULT_RAW_ROOT,
        run_documents as ingest_documents,
    )

    dcfg = load_json(DOC_CONFIG)
    batch_size = (
        int(batch_size_override)
        if batch_size_override is not None
        else int(dcfg["batch_size"])
    )
    if batch_size <= 0:
        raise ValueError("batch_size debe ser positivo")

    pending = pending_filings(db, ticker=ticker)
    if not pending:
        return {
            "status": "nothing_to_do",
            "selected_pending": 0,
            "batches_completed": 0,
            "filings_processed": 0,
            "batch_results": [],
        }

    batches = [
        pending[i:i + batch_size]
        for i in range(0, len(pending), batch_size)
    ]
    if max_batches is not None:
        if max_batches <= 0:
            raise ValueError("max_batches debe ser positivo")
        batches = batches[:max_batches]

    results: list[dict[str, object]] = []
    filings_processed = 0

    for index, batch in enumerate(batches, start=1):
        accessions = [x.accession_number for x in batch]
        result = ingest_documents(
            db=db,
            raw_root=DEFAULT_RAW_ROOT,
            accessions=accessions,
            max_filings=len(batch),
            max_files_per_filing=int(dcfg["max_files_per_filing"]),
            max_file_bytes=int(dcfg["max_file_bytes"]),
            max_index_bytes=int(dcfg["max_index_bytes"]),
            max_total_bytes=int(dcfg["max_total_bytes_per_batch"]),
            max_retry_after_seconds=120.0,
            rate_limit=float(dcfg["rate_limit_per_second"]),
            user_agent=os.environ["SEC_USER_AGENT"],
        )
        results.append(
            {
                "batch_index": index,
                "tickers": sorted({x.ticker for x in batch}),
                "first_acceptance": min(x.acceptance_datetime for x in batch),
                "last_acceptance": max(x.acceptance_datetime for x in batch),
                **result,
            }
        )
        filings_processed += len(batch)

        status = str(result.get("status", ""))
        print(
            json.dumps(
                {
                    "batch": index,
                    "of": len(batches),
                    "filings": len(batch),
                    "status": status,
                    "documents_downloaded":
                        result.get("documents_downloaded"),
                    "files_skipped": result.get("files_skipped"),
                    "errors": result.get("errors"),
                },
                ensure_ascii=False,
            ),
            flush=True,
        )
        if status not in {"completed", "completed_with_skips"}:
            raise RuntimeError(
                f"Batch {index} no completado limpiamente: {status}"
            )
        if result.get("errors"):
            raise RuntimeError(
                f"Batch {index} reportó errores: {result['errors']}"
            )

    return {
        "status": "completed",
        "selected_pending": len(pending),
        "batches_completed": len(results),
        "filings_processed": filings_processed,
        "batch_results": results,
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description=(
            "Deep SEC document scale using only filings compatible with "
            "daily-price market-state history."
        )
    )
    p.add_argument(
        "--stage",
        choices=("preflight", "documents", "audit"),
        required=True,
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--ticker")
    p.add_argument("--max-batches", type=int)
    p.add_argument("--batch-size", type=int)
    args = p.parse_args()

    if not args.db.is_file():
        raise FileNotFoundError(args.db)

    before = audit(args.db)
    if args.stage == "preflight":
        print(json.dumps(before, indent=2, ensure_ascii=False))
        return

    if args.stage == "documents":
        if before["status"] != "PASS":
            raise RuntimeError(
                "Preflight no pasó: "
                + json.dumps(before["failures"], ensure_ascii=False)
            )
        run_result = run_documents(
            args.db,
            ticker=args.ticker,
            max_batches=args.max_batches,
            batch_size_override=args.batch_size,
        )
        print(
            json.dumps(
                {
                    "document_run": run_result,
                    "audit_after": audit(args.db),
                },
                indent=2,
                ensure_ascii=False,
            )
        )
        return

    print(json.dumps(before, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

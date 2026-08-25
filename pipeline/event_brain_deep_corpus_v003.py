from __future__ import annotations

import argparse
import json
import sqlite3
import subprocess
import sys
from datetime import date, timedelta
from pathlib import Path

from ingestion.events.sec_event_normalizer_v003_deep import (
    NORMALIZATION_VERSION,
    normalize,
)
from features.events.event_state_v003_deep import (
    FEATURE_VERSION,
    build as build_states,
)
from evaluation.targets.event_reaction_targets_v003_deep import (
    LABEL_VERSION,
    build as build_labels,
)
from evaluation.events.deep_corpus_audit_v003 import corpus_audit

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
CONFIG_PATH = ROOT / "config" / "deep_event_corpus_v003.json"


def config() -> dict:
    return json.loads(CONFIG_PATH.read_text(encoding="utf-8"))


def _table_exists(conn: sqlite3.Connection, name: str) -> bool:
    return conn.execute(
        """
        SELECT 1 FROM sqlite_master
        WHERE (type='table' OR type='view') AND name=?
        """,
        (name,),
    ).fetchone() is not None


def cohort_window(
    conn: sqlite3.Connection,
    *,
    tickers: list[str],
    warmup_sessions: int,
) -> tuple[str, str, dict[str, dict[str, object]]]:
    placeholders = ",".join("?" for _ in tickers)
    rows = conn.execute(
        f"""
        WITH targets AS (
            SELECT asset_id,UPPER(ticker) AS ticker
            FROM assets
            WHERE UPPER(ticker) IN ({placeholders})
        ),
        days AS (
            SELECT DISTINCT g.asset_id,g.trading_day
            FROM daily_price_quality_gated_observations_v001 g
            JOIN targets t ON t.asset_id=g.asset_id
        ),
        ranked AS (
            SELECT
                d.asset_id,
                t.ticker,
                d.trading_day,
                ROW_NUMBER() OVER (
                    PARTITION BY d.asset_id ORDER BY d.trading_day
                ) AS seq,
                COUNT(*) OVER (
                    PARTITION BY d.asset_id
                ) AS n_days
            FROM days d
            JOIN targets t ON t.asset_id=d.asset_id
        )
        SELECT
            ticker,
            MAX(CASE WHEN seq=? THEN trading_day END) AS ready_day,
            MAX(trading_day) AS last_day,
            MAX(n_days) AS n_days
        FROM ranked
        GROUP BY ticker
        ORDER BY ticker
        """,
        [*tickers, warmup_sessions],
    ).fetchall()

    info = {
        str(ticker): {
            "ready_day": None if ready is None else str(ready),
            "last_day": None if last is None else str(last),
            "price_days": int(n or 0),
        }
        for ticker, ready, last, n in rows
    }
    missing = sorted(set(tickers) - set(info))
    if missing:
        raise RuntimeError(f"Tickers sin price history: {missing}")
    if any(x["ready_day"] is None for x in info.values()):
        raise RuntimeError("Algún ticker no llega al warmup de precios")

    common_start = max(str(x["ready_day"]) for x in info.values())
    common_end = min(str(x["last_day"]) for x in info.values())
    if common_start > common_end:
        raise RuntimeError(
            f"Ventana común inválida {common_start}>{common_end}"
        )
    return common_start, common_end, info


def _end_exclusive(day: str) -> str:
    return (date.fromisoformat(day) + timedelta(days=1)).isoformat()


def run_id_for(ticker: str) -> str:
    return f"eventbrain_deep_v003_{ticker.lower()}"


def normalization_runs(
    conn: sqlite3.Connection,
) -> dict[str, str]:
    rows = conn.execute(
        """
        SELECT nr.clustering_run_id,nr.normalization_run_id
        FROM event_normalization_runs nr
        WHERE nr.normalization_version=?
          AND nr.clustering_run_id LIKE 'eventbrain_deep_v003_%'
          AND nr.status='completed'
        """,
        (NORMALIZATION_VERSION,),
    ).fetchall()
    return {str(a): str(b) for a,b in rows}


def raw_corpus_counts(
    conn: sqlite3.Connection,
    *,
    tickers: list[str],
    forms: list[str],
    start: str,
    end_inclusive: str,
) -> dict[str, dict[str, int]]:
    tp = ",".join("?" for _ in tickers)
    fp = ",".join("?" for _ in forms)
    rows = conn.execute(
        f"""
        WITH latest_obs AS (
            SELECT
                o.filing_raw_document_id,
                o.metadata_version_id,
                ROW_NUMBER() OVER (
                    PARTITION BY o.filing_raw_document_id
                    ORDER BY o.observation_sequence DESC,
                             julianday(o.available_at) DESC,
                             o.metadata_observation_id DESC
                ) AS rn
            FROM sec_filing_metadata_observations o
        ),
        filings AS (
            SELECT
                v.filing_raw_document_id,
                UPPER(v.ticker_at_ingestion) AS ticker,
                UPPER(v.form) AS form,
                substr(v.acceptance_datetime,1,10) AS acceptance_day
            FROM latest_obs o
            JOIN sec_filing_metadata_versions v
              ON v.metadata_version_id=o.metadata_version_id
             AND v.filing_raw_document_id=o.filing_raw_document_id
            WHERE o.rn=1
              AND UPPER(v.ticker_at_ingestion) IN ({tp})
              AND UPPER(v.form) IN ({fp})
              AND substr(v.acceptance_datetime,1,10) BETWEEN ? AND ?
        )
        SELECT
            f.ticker,
            COUNT(DISTINCT f.filing_raw_document_id) AS filings,
            COUNT(DISTINCT sf.raw_document_id) AS raw_documents
        FROM filings f
        LEFT JOIN sec_filing_files sf
          ON sf.filing_raw_document_id=f.filing_raw_document_id
         AND sf.raw_document_id IS NOT NULL
        GROUP BY f.ticker
        ORDER BY f.ticker
        """,
        [*tickers, *forms, start, end_inclusive],
    ).fetchall()
    return {
        str(ticker): {
            "filings": int(filings or 0),
            "raw_documents": int(raw_documents or 0),
        }
        for ticker, filings, raw_documents in rows
    }


def preflight(db: Path) -> dict[str, object]:
    cfg = config()
    tickers = [str(x).upper() for x in cfg["tickers"]]
    forms = [str(x).upper() for x in cfg["forms"]]
    warmup = int(cfg["common_window"]["warmup_sessions"])

    with sqlite3.connect(db) as conn:
        required = [
            "assets",
            "daily_price_quality_gated_observations_v001",
            "sec_filing_metadata_observations",
            "sec_filing_metadata_versions",
            "sec_filing_files",
            "event_clustering_runs",
            "event_cluster_memberships",
        ]
        missing = [x for x in required if not _table_exists(conn,x)]
        if missing:
            raise RuntimeError(f"Contrato incompleto: {missing}")

        common_start, common_end, price_info = cohort_window(
            conn,
            tickers=tickers,
            warmup_sessions=warmup,
        )
        raw_counts = raw_corpus_counts(
            conn,
            tickers=tickers,
            forms=forms,
            start=common_start,
            end_inclusive=common_end,
        )

        existing_runs = {
            str(run_id): str(status)
            for run_id,status in conn.execute(
                """
                SELECT clustering_run_id,status
                FROM event_clustering_runs
                WHERE clustering_run_id LIKE 'eventbrain_deep_v003_%'
                """
            )
        }

    failures = []
    for ticker in tickers:
        if ticker not in raw_counts:
            failures.append(f"{ticker}:no_raw_documents_in_common_window")
        elif int(raw_counts[ticker]["raw_documents"]) == 0:
            failures.append(f"{ticker}:raw_documents=0")

    return {
        "status": "PASS" if not failures else "REVIEW",
        "failures": failures,
        "common_start": common_start,
        "common_end_inclusive": common_end,
        "clustering_end_exclusive": _end_exclusive(common_end),
        "price_windows": price_info,
        "raw_corpus_by_ticker": raw_counts,
        "total_filings_in_common_window": sum(
            x["filings"] for x in raw_counts.values()
        ),
        "total_raw_documents_in_common_window": sum(
            x["raw_documents"] for x in raw_counts.values()
        ),
        "existing_deep_clustering_runs": existing_runs,
    }


def cluster(
    db: Path,
    *,
    ticker: str | None,
) -> list[dict[str, object]]:
    pf = preflight(db)
    if pf["status"] != "PASS":
        raise RuntimeError(
            "Preflight FAIL: " + json.dumps(pf,ensure_ascii=False)
        )

    cfg = config()
    wanted = [str(x).upper() for x in cfg["tickers"]]
    if ticker:
        ticker = ticker.upper()
        if ticker not in wanted:
            raise ValueError(f"Ticker fuera del cohort: {ticker}")
        wanted = [ticker]

    results = []
    with sqlite3.connect(db) as conn:
        known = {
            str(run_id): str(status)
            for run_id,status in conn.execute(
                """
                SELECT clustering_run_id,status
                FROM event_clustering_runs
                WHERE clustering_run_id LIKE 'eventbrain_deep_v003_%'
                """
            )
        }

    for symbol in wanted:
        run_id = run_id_for(symbol)
        if known.get(run_id) == "completed":
            results.append({
                "ticker": symbol,
                "clustering_run_id": run_id,
                "status": "completed",
                "reused": True,
            })
            continue
        if run_id in known:
            raise RuntimeError(
                f"Run existente no reutilizable {run_id}: {known[run_id]}"
            )

        cmd = [
            sys.executable,
            "-m",
            "ingestion.events.deterministic_clustering",
            "--db",
            str(db),
            "--source",
            "sec",
            "--ticker",
            symbol,
            "--start",
            str(pf["common_start"]),
            "--end",
            str(pf["clustering_end_exclusive"]),
            "--max-documents",
            str(cfg["clustering"]["max_documents_per_ticker"]),
            "--run-id",
            run_id,
        ]
        print("$", " ".join(cmd), flush=True)
        completed = subprocess.run(
            cmd,
            cwd=ROOT,
            check=False,
            capture_output=True,
            text=True,
        )
        if completed.returncode != 0:
            tail = "\n".join(
                (completed.stderr or completed.stdout or "").splitlines()[-80:]
            )
            raise RuntimeError(
                f"Clustering subprocess falló para {symbol} "
                f"rc={completed.returncode}:\n{tail}"
            )

        with sqlite3.connect(db) as conn:
            row = conn.execute(
                """
                SELECT status,documents_considered,
                       memberships_written,clusters_created
                FROM event_clustering_runs
                WHERE clustering_run_id=?
                """,
                (run_id,),
            ).fetchone()
        if row is None or str(row[0]) != "completed":
            raise RuntimeError(f"Clustering no completado: {symbol}")
        results.append({
            "ticker": symbol,
            "clustering_run_id": run_id,
            "status": str(row[0]),
            "documents_considered": int(row[1] or 0),
            "memberships_written": int(row[2] or 0),
            "clusters_created": int(row[3] or 0),
            "reused": False,
        })
    return results


def normalize_all(db: Path) -> list[dict[str, object]]:
    cfg = config()
    tickers = [str(x).upper() for x in cfg["tickers"]]

    with sqlite3.connect(db) as conn:
        rows = {
            str(run): str(status)
            for run,status in conn.execute(
                """
                SELECT clustering_run_id,status
                FROM event_clustering_runs
                WHERE clustering_run_id LIKE 'eventbrain_deep_v003_%'
                """
            )
        }

    expected = [run_id_for(x) for x in tickers]
    missing = [x for x in expected if rows.get(x) != "completed"]
    if missing:
        raise RuntimeError(
            f"Faltan clustering runs completed: {missing}"
        )

    allowed_forms = [str(x).upper() for x in cfg["forms"]]
    results = []
    for run_id in expected:
        result = normalize(
            db,
            run_id,
            allowed_forms=allowed_forms,
        )
        print(json.dumps(
            {"clustering_run_id":run_id,**result},
            ensure_ascii=False,
        ),flush=True)
        results.append(result)
    return results


def states_all(db: Path) -> list[dict[str, object]]:
    cfg = config()
    tickers = [str(x).upper() for x in cfg["tickers"]]
    expected = [run_id_for(x) for x in tickers]

    with sqlite3.connect(db) as conn:
        runs = normalization_runs(conn)
    missing = [x for x in expected if x not in runs]
    if missing:
        raise RuntimeError(
            f"Faltan normalization runs v003: {missing}"
        )

    results = []
    for clustering_run_id in expected:
        result = build_states(db,runs[clustering_run_id])
        print(json.dumps(
            {"clustering_run_id":clustering_run_id,**result},
            ensure_ascii=False,
        ),flush=True)
        results.append(result)
    return results


def labels_all(db: Path) -> dict[str, object]:
    return build_labels(
        db,
        feature_version=FEATURE_VERSION,
        horizons=(1,3,5,10),
        include_intraday_coarse=False,
    )


def audit(db: Path) -> dict[str, object]:
    pf = preflight(db)
    corpus = corpus_audit(
        db,
        common_start=str(pf["common_start"]),
        common_end_inclusive=str(pf["common_end_inclusive"]),
    )

    cohort_deltas: dict[str, dict[str, int]] = {}
    for row in corpus.get("clustering", []):
        run_id = str(row["clustering_run_id"])
        prefix = "eventbrain_deep_v003_"
        if not run_id.startswith(prefix):
            continue
        ticker = run_id[len(prefix):].upper()
        expected_docs = int(
            pf.get("raw_corpus_by_ticker", {})
              .get(ticker, {})
              .get("raw_documents", 0)
        )
        actual_docs = int(row.get("documents_considered", 0) or 0)
        cohort_deltas[ticker] = {
            "cohort_raw_documents": expected_docs,
            "cluster_documents_considered": actual_docs,
            "delta": actual_docs - expected_docs,
        }

    return {
        "preflight": pf,
        "corpus": corpus,
        "cluster_vs_cohort_document_delta": cohort_deltas,
        "note": (
            "A small positive delta can come from legacy SEC forms already "
            "stored for the ticker. V003 normalization now applies the "
            "configured form allow-list before event identity creation."
        ),
    }


def main() -> None:
    p = argparse.ArgumentParser(
        description="Build homogeneous deep SEC Event Corpus V003"
    )
    p.add_argument(
        "--stage",
        choices=(
            "preflight",
            "cluster",
            "cluster-audit",
            "normalize",
            "states",
            "labels",
            "audit",
        ),
        required=True,
    )
    p.add_argument("--db",type=Path,default=DEFAULT_DB)
    p.add_argument("--ticker")
    args = p.parse_args()

    if not args.db.is_file():
        raise FileNotFoundError(args.db)

    if args.stage == "preflight":
        result = preflight(args.db)
    elif args.stage == "cluster":
        result = {
            "clustering":cluster(args.db,ticker=args.ticker),
            "audit":audit(args.db),
        }
    elif args.stage == "cluster-audit":
        result = audit(args.db)
    elif args.stage == "normalize":
        result = {
            "normalization":normalize_all(args.db),
            "audit":audit(args.db),
        }
    elif args.stage == "states":
        result = {
            "states":states_all(args.db),
            "audit":audit(args.db),
        }
    elif args.stage == "labels":
        result = {
            "labels":labels_all(args.db),
            "audit":audit(args.db),
        }
    else:
        result = audit(args.db)

    print(json.dumps(result,indent=2,ensure_ascii=False))


if __name__ == "__main__":
    main()

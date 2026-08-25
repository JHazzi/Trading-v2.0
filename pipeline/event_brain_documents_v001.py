from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DB = ROOT / "data" / "database" / "market_data_v2.db"
MANIFEST = ROOT / "config" / "event_brain_pilot_v001.json"


def manifest() -> dict:
    return json.loads(MANIFEST.read_text(encoding="utf-8"))


def selected_accessions(db: Path) -> list[tuple[str, str]]:
    cfg = manifest()
    tickers = [x["ticker"].upper() for x in cfg["assets"]]
    forms = cfg["sec_forms"]
    max_per = int(cfg["sec_max_filings_per_ticker"])
    with sqlite3.connect(db) as conn:
        rows = conn.execute(
            f"""
            WITH latest AS (
                SELECT
                    v.accession_number,
                    UPPER(v.ticker_at_ingestion) AS ticker,
                    v.form,
                    v.acceptance_datetime,
                    o.observation_sequence,
                    ROW_NUMBER() OVER (
                        PARTITION BY o.filing_raw_document_id
                        ORDER BY o.observation_sequence DESC
                    ) AS meta_rank
                FROM sec_filing_metadata_observations AS o
                JOIN sec_filing_metadata_versions AS v
                  ON v.metadata_version_id=o.metadata_version_id
                 AND v.filing_raw_document_id=o.filing_raw_document_id
            ),
            ranked AS (
                SELECT *,
                       ROW_NUMBER() OVER (
                           PARTITION BY ticker
                           ORDER BY acceptance_datetime DESC, accession_number
                       ) AS ticker_rank
                FROM latest
                WHERE meta_rank=1
                  AND ticker IN ({",".join("?" for _ in tickers)})
                  AND form IN ({",".join("?" for _ in forms)})
            )
            SELECT ticker, accession_number
            FROM ranked
            WHERE ticker_rank <= ?
            ORDER BY ticker, acceptance_datetime
            """,
            [*tickers, *forms, max_per],
        ).fetchall()
    return [(str(t), str(a)) for t, a in rows]


def audit(db: Path) -> dict[str, object]:
    with sqlite3.connect(db) as conn:
        by_ticker = conn.execute(
            """
            SELECT
                UPPER(v.ticker_at_ingestion) AS ticker,
                COUNT(DISTINCT v.filing_raw_document_id) AS filings,
                COUNT(DISTINCT ff.filing_raw_document_id) AS filings_with_inventory,
                COUNT(DISTINCT CASE
                    WHEN ff.raw_document_id IS NOT NULL THEN ff.filing_raw_document_id
                END) AS filings_with_downloaded_content,
                COUNT(DISTINCT CASE
                    WHEN ff.raw_document_id IS NOT NULL THEN ff.raw_document_id
                END) AS downloaded_raw_documents
            FROM sec_filing_metadata_versions AS v
            LEFT JOIN sec_filing_files AS ff
              ON ff.filing_raw_document_id=v.filing_raw_document_id
            WHERE v.ticker_at_ingestion IS NOT NULL
            GROUP BY UPPER(v.ticker_at_ingestion)
            ORDER BY ticker
            """
        ).fetchall()

        observations = conn.execute(
            """
            SELECT
                COUNT(*) AS observations,
                COUNT(DISTINCT raw_document_id) AS raw_documents,
                COUNT(DISTINCT retrieval_run_id) AS runs
            FROM sec_filing_file_observations
            """
        ).fetchone()

    return {
        "by_ticker": [
            {
                "ticker": r[0],
                "filings": int(r[1]),
                "filings_with_inventory": int(r[2]),
                "filings_with_downloaded_content": int(r[3]),
                "downloaded_raw_documents": int(r[4]),
            }
            for r in by_ticker
        ],
        "file_observations": int(observations[0] or 0),
        "unique_downloaded_raw_documents": int(observations[1] or 0),
        "document_ingestion_runs": int(observations[2] or 0),
    }


def run_documents(db: Path) -> None:
    if not os.environ.get("SEC_USER_AGENT"):
        raise RuntimeError("Falta SEC_USER_AGENT")
    pairs = selected_accessions(db)
    if not pairs:
        raise RuntimeError("No hay accessions seleccionados")
    accessions = [accession for _ticker, accession in pairs]

    for offset in range(0, len(accessions), 10):
        batch = accessions[offset:offset + 10]
        args = [
            sys.executable,
            "-m",
            "ingestion.events.sec_filing_documents_v2",
            "--max-filings", str(len(batch)),
            "--max-files-per-filing", "10",
            "--max-file-bytes", str(15 * 1024 * 1024),
            "--max-index-bytes", str(3 * 1024 * 1024),
            "--max-total-bytes", str(200 * 1024 * 1024),
            "--rate-limit", "2",
        ]
        for accession in batch:
            args.extend(["--accession", accession])
        print("$", " ".join(args), flush=True)
        subprocess.run(args, cwd=ROOT, check=True)


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument("--stage", choices=("audit", "documents"), required=True)
    p.add_argument("--db", type=Path, default=DB)
    a = p.parse_args()

    if a.stage == "documents":
        run_documents(a.db)
    print(json.dumps(audit(a.db), indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

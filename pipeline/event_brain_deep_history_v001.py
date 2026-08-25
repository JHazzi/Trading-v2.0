from __future__ import annotations

import argparse
import json
import os
import sqlite3
import subprocess
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
DEFAULT_CONFIG = ROOT / "config" / "event_brain_deep_history_v001.json"


def load_config(path: Path) -> dict:
    return json.loads(path.read_text(encoding="utf-8"))


def _latest_filing_rows(conn: sqlite3.Connection) -> list[sqlite3.Row]:
    conn.row_factory = sqlite3.Row
    return conn.execute(
        """
        WITH ranked AS (
            SELECT
                o.filing_raw_document_id,
                v.ticker_at_ingestion,
                v.cik,
                v.accession_number,
                v.form,
                v.acceptance_datetime,
                o.observation_kind,
                o.availability_is_point_in_time,
                ROW_NUMBER() OVER (
                    PARTITION BY o.filing_raw_document_id
                    ORDER BY o.observation_sequence DESC
                ) AS rn
            FROM sec_filing_metadata_observations AS o
            JOIN sec_filing_metadata_versions AS v
              ON v.metadata_version_id=o.metadata_version_id
             AND v.filing_raw_document_id=o.filing_raw_document_id
        )
        SELECT *
        FROM ranked
        WHERE rn=1
          AND ticker_at_ingestion IS NOT NULL
        ORDER BY UPPER(ticker_at_ingestion), acceptance_datetime
        """
    ).fetchall()


def preflight(db: Path, cfg: dict) -> dict:
    if int(cfg["sec_max_filings_per_target"]) < 100:
        raise RuntimeError(
            "Deep-history config demasiado pequeño; esperado >=100 filings/target"
        )

    auto = {str(t).upper() for t in cfg["automatic_tickers"]}
    explicit = cfg["explicit_issuer_targets"]
    explicit_tickers = {str(x["ticker"]).upper() for x in explicit}
    if auto & explicit_tickers:
        raise RuntimeError(
            "Un ticker no debe usar mapping automático y explicit CIK a la vez: "
            f"{sorted(auto & explicit_tickers)}"
        )

    with sqlite3.connect(db) as conn:
        migrations = {
            str(v): str(n)
            for v, n in conn.execute(
                "SELECT version,name FROM schema_migrations"
            )
        }
        required = {
            "016": "sec_filing_metadata_versioning",
            "017": "event_normalization",
            "018": "daily_price_asof",
            "019": "event_brain_v001",
        }
        bad = {
            version: (expected, migrations.get(version))
            for version, expected in required.items()
            if migrations.get(version) != expected
        }
        if bad:
            raise RuntimeError(f"Migraciones inválidas: {bad}")

        configured = sorted(auto | explicit_tickers)
        found = {
            str(r[0]).upper()
            for r in conn.execute(
                "SELECT ticker FROM assets"
            ).fetchall()
        }
        missing = [ticker for ticker in configured if ticker not in found]
        if missing:
            raise RuntimeError(f"Assets faltantes: {missing}")

    return {
        "status": "PASS",
        "configured_assets": len(auto | explicit_tickers),
        "automatic_tickers": sorted(auto),
        "explicit_issuer_targets": explicit,
        "max_filings_per_target": int(cfg["sec_max_filings_per_target"]),
    }


def run_metadata(cfg: dict) -> None:
    if not os.environ.get("SEC_USER_AGENT"):
        raise RuntimeError("Falta SEC_USER_AGENT")

    args = [
        sys.executable,
        "-m",
        "ingestion.events.sec_edgar_v4_history",
        "--forms",
        ",".join(cfg["sec_forms"]),
        "--max-filings",
        str(cfg["sec_max_filings_per_target"]),
        "--include-older",
        "--initial-availability-mode",
        "historical",
        "--rate-limit",
        "2",
    ]

    for ticker in cfg["automatic_tickers"]:
        args.extend(["--ticker", str(ticker)])

    for target in cfg["explicit_issuer_targets"]:
        args.extend([
            "--issuer",
            f"{str(target['ticker']).upper()}={target['cik']}",
        ])

    print("$", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)



def repair_aapl(cfg: dict) -> None:
    """Recover only AAPL after the legacy migration-016 collision."""
    if not os.environ.get("SEC_USER_AGENT"):
        raise RuntimeError("Falta SEC_USER_AGENT")

    args = [
        sys.executable,
        "-m",
        "ingestion.events.sec_edgar_v4_history",
        "--forms",
        ",".join(cfg["sec_forms"]),
        "--max-filings",
        str(cfg["sec_max_filings_per_target"]),
        "--include-older",
        "--initial-availability-mode",
        "historical",
        "--rate-limit",
        "2",
        "--ticker",
        "AAPL",
    ]
    print("$", " ".join(args), flush=True)
    subprocess.run(args, cwd=ROOT, check=True)


def audit(db: Path, cfg: dict) -> dict:
    target_tickers = sorted({
        *(str(t).upper() for t in cfg["automatic_tickers"]),
        *(str(x["ticker"]).upper() for x in cfg["explicit_issuer_targets"]),
    })

    with sqlite3.connect(db) as conn:
        conn.row_factory = sqlite3.Row
        rows = _latest_filing_rows(conn)
        allowed_forms = {str(form).upper() for form in cfg["sec_forms"]}
        rows = [
            row for row in rows
            if str(row["ticker_at_ingestion"]).upper() in target_tickers
            and str(row["form"]).upper() in allowed_forms
        ]

        by_ticker: dict[str, dict[str, object]] = {}
        by_year: dict[str, dict[str, int]] = {}
        by_form: dict[str, dict[str, int]] = {}
        by_cik: dict[str, dict[str, int]] = {}

        for row in rows:
            ticker = str(row["ticker_at_ingestion"]).upper()
            acceptance = str(row["acceptance_datetime"])
            year = acceptance[:4]
            form = str(row["form"])
            cik = str(row["cik"])

            t = by_ticker.setdefault(
                ticker,
                {
                    "filings": 0,
                    "first_acceptance": acceptance,
                    "last_acceptance": acceptance,
                    "ciks": set(),
                },
            )
            t["filings"] += 1
            t["first_acceptance"] = min(str(t["first_acceptance"]), acceptance)
            t["last_acceptance"] = max(str(t["last_acceptance"]), acceptance)
            t["ciks"].add(cik)

            by_year.setdefault(ticker, {}).setdefault(year, 0)
            by_year[ticker][year] += 1
            by_form.setdefault(ticker, {}).setdefault(form, 0)
            by_form[ticker][form] += 1
            by_cik.setdefault(ticker, {}).setdefault(cik, 0)
            by_cik[ticker][cik] += 1

        obs = conn.execute(
            """
            SELECT
                COUNT(*) AS n,
                SUM(CASE WHEN observation_kind='initial' THEN 1 ELSE 0 END) AS initial_n,
                SUM(CASE WHEN availability_is_point_in_time=1 THEN 1 ELSE 0 END) AS pit_n
            FROM sec_filing_metadata_observations
            """
        ).fetchone()

    serialized_ticker = {}
    for ticker, item in sorted(by_ticker.items()):
        first_year = int(str(item["first_acceptance"])[:4])
        last_year = int(str(item["last_acceptance"])[:4])
        serialized_ticker[ticker] = {
            "filings": int(item["filings"]),
            "first_acceptance": item["first_acceptance"],
            "last_acceptance": item["last_acceptance"],
            "calendar_year_span": last_year - first_year + 1,
            "ciks": sorted(item["ciks"]),
        }

    coverage_failures = []
    for ticker in target_tickers:
        info = serialized_ticker.get(ticker)
        if info is None:
            coverage_failures.append(f"{ticker}:no_metadata")
            continue
        if int(info["calendar_year_span"]) < 6:
            coverage_failures.append(
                f"{ticker}:history_span<{6}y"
            )

    return {
        "status": "PASS" if not coverage_failures else "REVIEW",
        "coverage_failures": coverage_failures,
        "target_tickers": target_tickers,
        "by_ticker": serialized_ticker,
        "by_year": by_year,
        "by_form": by_form,
        "by_cik": by_cik,
        "metadata_observations_total": int(obs["n"] or 0),
        "initial_metadata_observations": int(obs["initial_n"] or 0),
        "pit_metadata_observations": int(obs["pit_n"] or 0),
        "next_stage_allowed": (
            "document scale only after reviewing actual historical coverage"
        ),
    }


def main() -> None:
    p = argparse.ArgumentParser()
    p.add_argument(
        "--stage",
        choices=("preflight", "metadata", "repair-aapl", "audit"),
        required=True,
    )
    p.add_argument("--db", type=Path, default=DEFAULT_DB)
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    args = p.parse_args()

    cfg = load_config(args.config)

    if args.stage == "preflight":
        result = preflight(args.db, cfg)
    elif args.stage == "metadata":
        preflight(args.db, cfg)
        run_metadata(cfg)
        result = audit(args.db, cfg)
    elif args.stage == "repair-aapl":
        preflight(args.db, cfg)
        repair_aapl(cfg)
        result = audit(args.db, cfg)
    else:
        result = audit(args.db, cfg)

    print(json.dumps(result, indent=2, ensure_ascii=False))


if __name__ == "__main__":
    main()

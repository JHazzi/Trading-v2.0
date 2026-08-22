#!/usr/bin/env python3
"""Auditoría inicial de la base legacy antes de migrarla.

No modifica la base vieja. Sólo genera un reporte JSON y lo imprime por stdout.
"""

from __future__ import annotations

import argparse
import json
import sqlite3
from collections import defaultdict
from pathlib import Path

EXPECTED_TABLES = [
    "universo_tickers",
    "precios",
    "noticias",
    "relaciones_organicas",
    "macro_diario",
    "correlaciones",
    "vectores_estado",
    "paper_trading",
]


def table_exists(conn: sqlite3.Connection, table: str) -> bool:
    row = conn.execute(
        "SELECT 1 FROM sqlite_master WHERE type='table' AND name=?",
        (table,),
    ).fetchone()
    return row is not None


def columns(conn: sqlite3.Connection, table: str) -> list[str]:
    return [row[1] for row in conn.execute(f'PRAGMA table_info("{table}")')]


def safe_count(conn: sqlite3.Connection, table: str) -> int | None:
    if not table_exists(conn, table):
        return None
    return int(conn.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0])


def audit(db_path: Path) -> dict:
    if not db_path.exists():
        raise FileNotFoundError(db_path)

    conn = sqlite3.connect(db_path)
    report: dict = {
        "database": str(db_path),
        "tables": {},
        "checks": {},
    }

    try:
        for table in EXPECTED_TABLES:
            exists = table_exists(conn, table)
            report["tables"][table] = {
                "exists": exists,
                "rows": safe_count(conn, table),
                "columns": columns(conn, table) if exists else [],
            }

        if table_exists(conn, "precios"):
            report["checks"]["price_timestamps"] = conn.execute(
                """
                SELECT ticker,
                       MIN(timestamp) AS first_timestamp,
                       MAX(timestamp) AS last_timestamp,
                       COUNT(*) AS rows
                FROM precios
                GROUP BY ticker
                ORDER BY rows DESC
                LIMIT 20
                """
            ).fetchall()

            report["checks"]["price_duplicate_keys"] = int(
                conn.execute(
                    """
                    SELECT COUNT(*) FROM (
                        SELECT ticker, timestamp
                        FROM precios
                        GROUP BY ticker, timestamp
                        HAVING COUNT(*) > 1
                    )
                    """
                ).fetchone()[0]
            )

        if table_exists(conn, "noticias"):
            report["checks"]["news_sources"] = conn.execute(
                """
                SELECT COALESCE(fuente, '(NULL)') AS fuente,
                       COUNT(*) AS rows
                FROM noticias
                GROUP BY fuente
                ORDER BY rows DESC
                LIMIT 30
                """
            ).fetchall()

            report["checks"]["news_missing_sentiment"] = int(
                conn.execute(
                    "SELECT COUNT(*) FROM noticias WHERE sentimiento IS NULL"
                ).fetchone()[0]
            )

            report["checks"]["news_missing_importance"] = int(
                conn.execute(
                    "SELECT COUNT(*) FROM noticias WHERE importancia IS NULL"
                ).fetchone()[0]
            )

        if table_exists(conn, "correlaciones"):
            report["checks"]["legacy_targets"] = conn.execute(
                """
                SELECT COUNT(*) AS rows,
                       COUNT(impacto_mfe_60m_pct) AS non_null_mfe,
                       MIN(impacto_mfe_60m_pct) AS min_mfe,
                       MAX(impacto_mfe_60m_pct) AS max_mfe,
                       AVG(impacto_mfe_60m_pct) AS avg_mfe
                FROM correlaciones
                """
            ).fetchone()

        if table_exists(conn, "macro_diario"):
            report["checks"]["macro_range"] = conn.execute(
                """
                SELECT MIN(fecha), MAX(fecha), COUNT(*)
                FROM macro_diario
                """
            ).fetchone()

        # Convert tuples to JSON-safe lists.
        for key, value in list(report["checks"].items()):
            if isinstance(value, list):
                report["checks"][key] = [list(row) for row in value]
            elif isinstance(value, tuple):
                report["checks"][key] = list(value)

    finally:
        conn.close()

    return report


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("db", type=Path, help="Ruta a quant_market_bot/data/market_data.db")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("legacy_audit_report.json"),
        help="Archivo JSON de salida",
    )
    args = parser.parse_args()

    report = audit(args.db)
    args.output.write_text(json.dumps(report, indent=2, ensure_ascii=False), encoding="utf-8")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print(f"\nReporte guardado en: {args.output}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

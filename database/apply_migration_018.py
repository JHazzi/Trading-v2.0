from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
MIGRATION = ROOT / "database" / "migrations" / "018_daily_price_asof.sql"

REQUIRED_TABLES = {"daily_price_asof_configs"}
REQUIRED_VIEWS = {
    "daily_price_quality_eligible_retrievals_v001",
    "daily_price_quality_gated_observations_v001",
}


def _statements(sql: str) -> list[str]:
    out: list[str] = []
    buf = ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            buf = ""
            if stmt and not stmt.upper().startswith("PRAGMA FOREIGN_KEYS"):
                out.append(stmt)
    if buf.strip():
        raise RuntimeError("SQL incompleto al final de 018_daily_price_asof.sql")
    return out


def apply(db: Path) -> dict[str, object]:
    if not db.is_file():
        raise FileNotFoundError(f"DB inexistente: {db}")
    if not MIGRATION.is_file():
        raise FileNotFoundError(
            f"Falta {MIGRATION}. El repo actual ya contiene 018_daily_price_asof.sql."
        )

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        migrations = {
            str(v): str(n)
            for v, n in conn.execute(
                "SELECT version, name FROM schema_migrations"
            )
        }
        if migrations.get("013") != "daily_price_observation_foundation":
            raise RuntimeError(
                "018 requiere 013|daily_price_observation_foundation"
            )
        existing = migrations.get("018")
        if existing not in (None, "daily_price_asof"):
            raise RuntimeError(f"Colisión de migración 018: {existing!r}")

        sql = MIGRATION.read_text(encoding="utf-8")
        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in _statements(sql):
                conn.execute(stmt)

            tables = {
                str(r[0])
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            views = {
                str(r[0])
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='view'"
                )
            }
            missing_tables = sorted(REQUIRED_TABLES - tables)
            missing_views = sorted(REQUIRED_VIEWS - views)
            if missing_tables or missing_views:
                raise RuntimeError(
                    f"018 incompleta: tables={missing_tables}, views={missing_views}"
                )

            row = conn.execute(
                "SELECT name FROM schema_migrations WHERE version='018'"
            ).fetchone()
            if row != ("daily_price_asof",):
                raise RuntimeError(f"Identidad 018 inválida: {row}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "migration": "018_daily_price_asof",
        "db": str(db),
        "status": "applied",
        "tables": sorted(REQUIRED_TABLES),
        "views": sorted(REQUIRED_VIEWS),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()
    print(apply(args.db))


if __name__ == "__main__":
    main()

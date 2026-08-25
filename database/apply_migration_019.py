from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_DB = ROOT / "data" / "database" / "market_data_v2.db"
MIGRATION = ROOT / "database" / "migrations" / "019_event_brain_v001.sql"

REQUIRED = {
    "event_state_feature_configs",
    "normalized_event_state_snapshots",
    "normalized_event_reaction_labels",
    "event_brain_training_runs",
}


def _statements(sql: str) -> list[str]:
    out, buf = [], ""
    for line in sql.splitlines(keepends=True):
        buf += line
        if sqlite3.complete_statement(buf):
            stmt = buf.strip()
            buf = ""
            if stmt and not stmt.upper().startswith("PRAGMA FOREIGN_KEYS"):
                out.append(stmt)
    if buf.strip():
        raise RuntimeError("SQL incompleto al final de 019")
    return out


def apply(db: Path) -> dict[str, object]:
    if not db.is_file():
        raise FileNotFoundError(f"DB inexistente: {db}")
    sql = MIGRATION.read_text(encoding="utf-8")

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        migrations = {
            str(v): str(n)
            for v, n in conn.execute(
                "SELECT version, name FROM schema_migrations"
            )
        }
        expected = {
            "017": "event_normalization",
            "018": "daily_price_asof",
        }
        for version, name in expected.items():
            if migrations.get(version) != name:
                raise RuntimeError(
                    f"019 requiere {version}|{name}; actual={migrations.get(version)!r}"
                )
        if migrations.get("019") not in (None, "event_brain_v001"):
            raise RuntimeError(
                f"Colisión de migración 019: {migrations.get('019')!r}"
            )

        conn.execute("BEGIN IMMEDIATE")
        try:
            for stmt in _statements(sql):
                conn.execute(stmt)
            existing = {
                str(r[0])
                for r in conn.execute(
                    "SELECT name FROM sqlite_master WHERE type='table'"
                )
            }
            missing = sorted(REQUIRED - existing)
            if missing:
                raise RuntimeError(f"019 incompleta; faltan {missing}")
            row = conn.execute(
                "SELECT name FROM schema_migrations WHERE version='019'"
            ).fetchone()
            if row != ("event_brain_v001",):
                raise RuntimeError(f"Identidad 019 inválida: {row}")
            conn.commit()
        except Exception:
            conn.rollback()
            raise

    return {
        "migration": "019_event_brain_v001",
        "db": str(db),
        "status": "applied",
        "tables": sorted(REQUIRED),
    }


def main() -> None:
    ap = argparse.ArgumentParser()
    ap.add_argument("--db", type=Path, default=DEFAULT_DB)
    args = ap.parse_args()
    print(apply(args.db))


if __name__ == "__main__":
    main()

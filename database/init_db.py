#!/usr/bin/env python3
"""Initialize the Quant Market AI SQLite database from schema.sql."""

from __future__ import annotations

import argparse
import sqlite3
from pathlib import Path


def initialize_database(schema_path: Path, db_path: Path) -> None:
    db_path.parent.mkdir(parents=True, exist_ok=True)

    if db_path.exists():
        raise FileExistsError(
            f"La base ya existe: {db_path}. "
            "No se sobrescribe automáticamente. Elegí otra ruta o eliminála de forma explícita."
        )

    schema = schema_path.read_text(encoding="utf-8")

    conn = sqlite3.connect(db_path)
    try:
        conn.execute("PRAGMA foreign_keys = ON;")
        conn.executescript(schema)
        conn.commit()
    except Exception:
        conn.rollback()
        raise
    finally:
        conn.close()

    print(f"Base creada correctamente: {db_path}")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument(
        "--schema",
        type=Path,
        default=Path(__file__).with_name("schema.sql"),
        help="Ruta a schema.sql",
    )
    parser.add_argument(
        "--db",
        type=Path,
        default=Path(__file__).parents[1] / "data" / "database" / "market_data_v2.db",
        help="Ruta de la nueva base SQLite",
    )
    args = parser.parse_args()

    initialize_database(args.schema, args.db)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT_DB = ROOT / "database" / "init_db.py"

NEW_MIGRATIONS = [
    ("018", "018_daily_price_asof.sql"),
    ("019", "019_event_brain_v001.sql"),
]

NEW_TABLES = [
    "daily_price_asof_configs",
    "event_state_feature_configs",
    "normalized_event_state_snapshots",
    "normalized_event_reaction_labels",
    "event_brain_training_runs",
]


def main() -> None:
    text = INIT_DB.read_text(encoding="utf-8")
    original = text

    # User's local repo already contains 017 after the previous step.
    for version, filename in NEW_MIGRATIONS:
        needle = f'    ("{version}", "{filename}"),'
        if needle in text:
            continue

        previous = str(int(version) - 1).zfill(3)
        prev_marker = f'    ("{previous}", '
        pos = text.find(prev_marker)
        if pos < 0:
            raise RuntimeError(
                f"No encuentro migración {previous} en init_db.py; "
                f"no voy a insertar {version} en una cadena desconocida."
            )
        line_end = text.find("\n", pos)
        text = text[: line_end + 1] + needle + "\n" + text[line_end + 1 :]

    marker = "REQUIRED_CURRENT_TABLES = {"
    start = text.find(marker)
    if start < 0:
        raise RuntimeError("No encuentro REQUIRED_CURRENT_TABLES")
    end = text.find("\n}", start)
    if end < 0:
        raise RuntimeError("No encuentro cierre de REQUIRED_CURRENT_TABLES")

    missing_tables = [t for t in NEW_TABLES if f'"{t}"' not in text[start:end]]
    if missing_tables:
        insertion = "".join(f'\n    "{t}",' for t in missing_tables)
        text = text[:end] + insertion + text[end:]

    if text != original:
        INIT_DB.write_text(text, encoding="utf-8")

    print({
        "init_db_changed": text != original,
        "migrations": [v for v, _ in NEW_MIGRATIONS],
        "tables_added_if_missing": missing_tables,
    })


if __name__ == "__main__":
    main()

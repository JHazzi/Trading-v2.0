from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
INIT_DB = ROOT / "database" / "init_db.py"

MIGRATION = '    ("017", "017_event_normalization.sql"),'
TABLES = [
    "event_normalization_configs",
    "event_normalization_runs",
    "normalized_event_identities",
    "normalized_event_versions",
    "normalized_event_observations",
    "event_cluster_event_links",
    "event_evidence_semantics",
    "normalized_event_entity_links",
    "normalized_event_asset_links",
]


def main() -> None:
    text = INIT_DB.read_text(encoding="utf-8")
    original = text

    if "017_event_normalization.sql" not in text:
        anchor = '    ("016", "016_sec_filing_metadata_versioning.sql"),'
        if anchor not in text:
            raise RuntimeError(
                "No encuentro 016 en init_db.py; no voy a adivinar la cadena de bootstrap."
            )
        text = text.replace(anchor, anchor + "\n" + MIGRATION, 1)

    missing_tables = [t for t in TABLES if f'"{t}"' not in text]
    if missing_tables:
        # Insert before the closing brace of REQUIRED_CURRENT_TABLES.
        marker = "REQUIRED_CURRENT_TABLES = {"
        start = text.find(marker)
        if start < 0:
            raise RuntimeError("No encuentro REQUIRED_CURRENT_TABLES en init_db.py")
        end = text.find("\n}", start)
        if end < 0:
            raise RuntimeError("No encuentro cierre de REQUIRED_CURRENT_TABLES")
        insertion = "".join(f'\n    "{t}",' for t in missing_tables)
        text = text[:end] + insertion + text[end:]

    if text != original:
        INIT_DB.write_text(text, encoding="utf-8")

    print({
        "migration": "017_event_normalization",
        "init_db_changed": text != original,
        "tables_added_if_missing": missing_tables,
    })


if __name__ == "__main__":
    main()

from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

INIT_DB = ROOT / "database" / "init_db.py"
BOOTSTRAP_TEST = ROOT / "tests" / "test_database_bootstrap.py"

MIGRATION_LINE = '    ("016", "016_sec_filing_metadata_versioning.sql"),'
TABLE_LINES = [
    '    "sec_submission_retrievals",',
    '    "sec_filing_metadata_versions",',
    '    "sec_filing_metadata_observations",',
    '    "sec_filing_document_metadata_selections",',
]


def replace_once(text: str, old: str, new: str, path: Path) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"{path}: esperaba exactamente una coincidencia de {old!r}, encontré {count}"
        )
    return text.replace(old, new, 1)


def patch_init_db() -> bool:
    path = INIT_DB
    text = path.read_text(encoding="utf-8")
    original = text

    if "016_sec_filing_metadata_versioning.sql" not in text:
        text = replace_once(
            text,
            '    ("015", "015_deterministic_event_clustering.sql"),\n)',
            '    ("015", "015_deterministic_event_clustering.sql"),\n'
            f'{MIGRATION_LINE}\n)',
            path,
        )

    if '"sec_submission_retrievals"' not in text:
        marker = '    "event_cluster_sec_observation_refs",\n}'
        replacement = (
            '    "event_cluster_sec_observation_refs",\n'
            + "\n".join(TABLE_LINES)
            + "\n}"
        )
        text = replace_once(text, marker, replacement, path)

    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def patch_bootstrap_test() -> bool:
    path = BOOTSTRAP_TEST
    text = path.read_text(encoding="utf-8")
    original = text

    if "apply_migration_016" not in text:
        text = replace_once(
            text,
            "from database.apply_migration_015 import apply as apply_migration_015\n",
            "from database.apply_migration_015 import apply as apply_migration_015\n"
            "from database.apply_migration_016 import apply as apply_migration_016\n",
            path,
        )

    if 'migration_names["016"]' not in text:
        text = replace_once(
            text,
            '        assert migration_names["015"] == "deterministic_event_clustering"\n',
            '        assert migration_names["015"] == "deterministic_event_clustering"\n'
            '        assert migration_names["016"] == "sec_filing_metadata_versioning"\n',
            path,
        )

    if "        apply_migration_016,\n" not in text:
        text = replace_once(
            text,
            "        apply_migration_015,\n    )",
            "        apply_migration_015,\n"
            "        apply_migration_016,\n"
            "    )",
            path,
        )

    text = text.replace(
        "WHERE version BETWEEN '012' AND '015'",
        "WHERE version BETWEEN '012' AND '016'",
    )

    if '"016": "sec_filing_metadata_versioning",' not in text:
        text = replace_once(
            text,
            '        "015": "deterministic_event_clustering",\n    }',
            '        "015": "deterministic_event_clustering",\n'
            '        "016": "sec_filing_metadata_versioning",\n'
            "    }",
            path,
        )

    if text != original:
        path.write_text(text, encoding="utf-8")
        return True
    return False


def main() -> None:
    missing = [p for p in (INIT_DB, BOOTSTRAP_TEST) if not p.is_file()]
    if missing:
        raise SystemExit(f"Faltan archivos esperados: {missing}")

    changed_init = patch_init_db()
    changed_test = patch_bootstrap_test()

    print(
        {
            "init_db_changed": changed_init,
            "bootstrap_test_changed": changed_test,
            "migration": "016_sec_filing_metadata_versioning",
        }
    )


if __name__ == "__main__":
    main()

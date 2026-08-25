from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingestion" / "events" / "deterministic_clustering.py"


def replace_once(text: str, old: str, new: str) -> str:
    count = text.count(old)
    if count != 1:
        raise RuntimeError(
            f"Esperaba una coincidencia exacta, encontré {count}:\n{old}"
        )
    return text.replace(old, new, 1)


def main() -> None:
    text = TARGET.read_text(encoding="utf-8")
    original = text

    old_select_tail = '''            ff.document_name,
            ff.description
        FROM sec_filing_file_versions AS v
'''
    new_select_tail = '''            ff.document_name,
            ff.description,
            COALESCE((
                SELECT mo.availability_is_point_in_time
                FROM sec_filing_metadata_observations AS mo
                WHERE mo.filing_raw_document_id = v.filing_raw_document_id
                  AND julianday(mo.available_at) =
                      julianday(sf.acceptance_datetime)
                ORDER BY mo.observation_sequence
                LIMIT 1
            ), 0) AS acceptance_availability_is_point_in_time
        FROM sec_filing_file_versions AS v
'''
    if "acceptance_availability_is_point_in_time" not in text:
        text = replace_once(text, old_select_tail, new_select_tail)

    old_flag = '''            availability_is_point_in_time=True,
            asset_ids=_asset_ids_for_raw(
'''
    new_flag = '''            availability_is_point_in_time=(
                bool(row[13])
                if available_at == acceptance_at
                else True
            ),
            asset_ids=_asset_ids_for_raw(
'''
    if "bool(row[13])" not in text:
        text = replace_once(text, old_flag, new_flag)

    validation_start = text.find("def _validate_runtime_schema")
    if validation_start < 0:
        raise RuntimeError("No encuentro _validate_runtime_schema")
    if '"sec_filing_metadata_observations",' not in text[validation_start:]:
        old_required = '''        "sec_filing_file_observations",
        "event_clustering_configs",
'''
        new_required = '''        "sec_filing_file_observations",
        "sec_filing_metadata_observations",
        "event_clustering_configs",
'''
        text = replace_once(text, old_required, new_required)

    if '"sec_per_evidence_pit_flag"' not in text:
        old_contract = '''            "point_in_time_sec"
            if source == "sec"
'''
        new_contract = '''            "sec_per_evidence_pit_flag"
            if source == "sec"
'''
        text = replace_once(text, old_contract, new_contract)

    if text != original:
        TARGET.write_text(text, encoding="utf-8")

    print({
        "target": str(TARGET),
        "changed": text != original,
        "historical_acceptance_inherits_metadata_pit_flag": True,
        "retrieval_time_revision_is_pit": True,
    })


if __name__ == "__main__":
    main()

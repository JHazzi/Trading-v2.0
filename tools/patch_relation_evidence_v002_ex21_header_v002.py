from __future__ import annotations

import argparse
import json
import re
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "knowledge" / "relations" / "relation_evidence_extraction_v002.py"

RULE_PATTERN = r"\borganized\s+or\s+incorporated\b"
ANCHOR_PATTERN = r"\bwhere\s+incorporated\b"


def inspect_source(text: str) -> dict:
    result = {
        "status": "PASS",
        "target": str(TARGET.relative_to(ROOT)),
        "already_applied": False,
        "would_apply": False,
        "failures": [],
    }

    if "BAD_NAME_PHRASES = re.compile(" not in text:
        result["status"] = "FAIL"
        result["failures"].append("BAD_NAME_PHRASES_block_not_found")
        return result

    if "def quality_flags(" not in text:
        result["status"] = "FAIL"
        result["failures"].append("quality_flags_function_not_found")
        return result

    # Extract only the BAD_NAME_PHRASES assignment so we don't accidentally
    # match unrelated text elsewhere in the module.
    match = re.search(
        r"BAD_NAME_PHRASES\s*=\s*re\.compile\(\s*(?P<body>r?[\"']{3}.*?[\"']{3})"
        r"\s*,\s*re\.I\s*\|\s*re\.X\s*,?\s*\)",
        text,
        flags=re.S,
    )
    if match is None:
        result["status"] = "FAIL"
        result["failures"].append("BAD_NAME_PHRASES_structure_unrecognized")
        return result

    body = match.group("body")
    if RULE_PATTERN in body:
        result["already_applied"] = True
        return result

    if ANCHOR_PATTERN not in body:
        result["status"] = "FAIL"
        result["failures"].append("where_incorporated_anchor_not_found_in_BAD_NAME_PHRASES")
        return result

    result["would_apply"] = True
    return result


def patch_text(text: str) -> str:
    inspection = inspect_source(text)
    if inspection["status"] != "PASS":
        raise RuntimeError(
            "refusing to patch: " + ", ".join(inspection["failures"])
        )
    if inspection["already_applied"]:
        return text

    # Insert immediately after the existing `where incorporated` alternative.
    line_re = re.compile(
        r"(?P<indent>^[ \t]*)\|\\bwhere\\s\+incorporated\\b[ \t]*$",
        flags=re.M,
    )
    match = line_re.search(text)
    if match is None:
        raise RuntimeError(
            "validated BAD_NAME_PHRASES but exact insertion line was not found"
        )

    indent = match.group("indent")
    insertion = (
        match.group(0)
        + "\n"
        + indent
        + r"|\borganized\s+or\s+incorporated\b"
    )
    patched = text[:match.start()] + insertion + text[match.end():]

    post = inspect_source(patched)
    if post["status"] != "PASS" or not post["already_applied"]:
        raise RuntimeError("post-patch validation failed")
    return patched


def main() -> None:
    parser = argparse.ArgumentParser()
    mode = parser.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = parser.parse_args()

    if not TARGET.is_file():
        raise FileNotFoundError(TARGET)

    text = TARGET.read_text(encoding="utf-8")
    inspection = inspect_source(text)

    if args.check:
        print(json.dumps(inspection, indent=2))
        if inspection["status"] != "PASS":
            raise SystemExit(2)
        return

    if inspection["status"] != "PASS":
        raise RuntimeError(
            "refusing to patch: " + ", ".join(inspection["failures"])
        )
    if inspection["already_applied"]:
        print(json.dumps({
            "status": "PASS",
            "result": "already_applied",
        }, indent=2))
        return

    patched = patch_text(text)
    TARGET.write_text(patched, encoding="utf-8")

    print(json.dumps({
        "status": "PASS",
        "result": "applied",
        "target": str(TARGET.relative_to(ROOT)),
        "rule_added": RULE_PATTERN,
    }, indent=2))


if __name__ == "__main__":
    main()

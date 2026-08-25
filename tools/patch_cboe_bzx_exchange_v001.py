from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingestion" / "prices" / "yahoo_daily_v1.py"

ALIASES = (
    '    "BATS": "BATS",\n'
    '    "BZX": "BATS",\n'
    '    "BTS": "BATS",\n'
    '    "CBOE BZX": "BATS",\n'
    '    "BZX EQUITIES": "BATS",\n'
)
CALENDAR = '    "BATS": "BATS",\n'

CANONICAL_ANCHOR = '    "NCM": "XNAS",\n'
CALENDAR_ANCHOR = '    "XNAS": "XNAS",\n'

MARKER = '"BATS": "BATS"'


def patched_text(source: str) -> tuple[str, bool]:
    canonical_start = source.find("EXCHANGE_CANONICAL_MAP = {")
    calendar_start = source.find("EXCHANGE_CALENDAR_MAP = {")
    if canonical_start < 0 or calendar_start < 0:
        raise RuntimeError("Exchange map anchors not found")

    canonical_end = source.find("}\n", canonical_start)
    calendar_end = source.find("}\n", calendar_start)
    if canonical_end < 0 or calendar_end < 0:
        raise RuntimeError("Exchange map endings not found")

    canonical_block = source[canonical_start:canonical_end]
    calendar_block = source[calendar_start:calendar_end]

    canonical_has = '"BATS": "BATS"' in canonical_block
    calendar_has = '"BATS": "BATS"' in calendar_block

    changed = False
    if not canonical_has:
        if CANONICAL_ANCHOR not in source:
            raise RuntimeError("Canonical exchange insertion anchor not found")
        source = source.replace(
            CANONICAL_ANCHOR,
            CANONICAL_ANCHOR + ALIASES,
            1,
        )
        changed = True

    if not calendar_has:
        # Recompute calendar position after the first insertion but use the
        # first XNAS entry *after* EXCHANGE_CALENDAR_MAP.
        calendar_start = source.find("EXCHANGE_CALENDAR_MAP = {")
        before = source[:calendar_start]
        after = source[calendar_start:]
        if CALENDAR_ANCHOR not in after:
            raise RuntimeError("Calendar exchange insertion anchor not found")
        after = after.replace(
            CALENDAR_ANCHOR,
            CALENDAR_ANCHOR + CALENDAR,
            1,
        )
        source = before + after
        changed = True

    return source, changed


def main() -> None:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = p.parse_args()

    if not TARGET.is_file():
        raise FileNotFoundError(TARGET)

    source = TARGET.read_text(encoding="utf-8")
    result, changed = patched_text(source)

    if args.check:
        print("would_apply" if changed else "already_applied")
        return

    if changed:
        TARGET.write_text(result, encoding="utf-8")
        print(f"applied {TARGET}")
    else:
        print(f"already_applied {TARGET}")


if __name__ == "__main__":
    main()

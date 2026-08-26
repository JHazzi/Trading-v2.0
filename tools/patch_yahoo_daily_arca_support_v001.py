from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
TARGET = ROOT / "ingestion" / "prices" / "yahoo_daily_v1.py"

OLD_CANONICAL = '''    "NCM": "XNAS",
}
EXCHANGE_CALENDAR_MAP = {
    "XNYS": "XNYS",
    "XNAS": "XNAS",
}
'''

NEW_CANONICAL = '''    "NCM": "XNAS",
    # NYSE Arca identity. Calendar proxy is XNYS because ARCA-listed ETFs
    # follow the U.S. equity regular-session holiday/close schedule used here.
    "ARCX": "ARCX",
    "ARCA": "ARCX",
    "NYSE ARCA": "ARCX",
    "NYSEARCA": "ARCX",
    "PCX": "ARCX",
}
EXCHANGE_CALENDAR_MAP = {
    "XNYS": "XNYS",
    "XNAS": "XNAS",
    "ARCX": "XNYS",
}
'''


def status() -> str:
    if not TARGET.is_file():
        return "missing_target"
    text = TARGET.read_text(encoding="utf-8")
    if NEW_CANONICAL in text:
        return "already_applied"
    if OLD_CANONICAL in text:
        return "ready"
    return "unexpected_source"


def apply() -> str:
    st = status()
    if st == "already_applied":
        return st
    if st != "ready":
        raise RuntimeError(
            f"Cannot patch {TARGET}: source status={st}. "
            "Inspect local changes instead of forcing the patch."
        )
    text = TARGET.read_text(encoding="utf-8")
    TARGET.write_text(text.replace(OLD_CANONICAL, NEW_CANONICAL, 1), encoding="utf-8")
    return "applied"


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = p.parse_args()
    print(apply() if a.apply else status())


if __name__ == "__main__":
    main()

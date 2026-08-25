from __future__ import annotations

import importlib.util
from pathlib import Path

from tools.patch_cboe_bzx_exchange_v001 import patched_text


BASE = """\
EXCHANGE_CANONICAL_MAP = {
    "XNYS": "XNYS",
    "NYSE": "XNYS",
    "NYQ": "XNYS",
    "XNAS": "XNAS",
    "NASDAQ": "XNAS",
    "NMS": "XNAS",
    "NGM": "XNAS",
    "NCM": "XNAS",
}
EXCHANGE_CALENDAR_MAP = {
    "XNYS": "XNYS",
    "XNAS": "XNAS",
}
"""


def test_patch_adds_bzx_aliases_and_calendar():
    result, changed = patched_text(BASE)
    assert changed is True
    assert '"BATS": "BATS"' in result
    assert '"BZX": "BATS"' in result
    assert '"BTS": "BATS"' in result
    assert '"CBOE BZX": "BATS"' in result
    assert '"BZX EQUITIES": "BATS"' in result

    calendar = result.split("EXCHANGE_CALENDAR_MAP = {", 1)[1]
    assert '"BATS": "BATS"' in calendar


def test_patch_is_idempotent():
    first, changed1 = patched_text(BASE)
    second, changed2 = patched_text(first)
    assert changed1 is True
    assert changed2 is False
    assert first == second


def test_exchange_calendars_supports_bats():
    import exchange_calendars
    assert "BATS" in exchange_calendars.get_calendar_names()
    cal = exchange_calendars.get_calendar("BATS")
    assert cal is not None

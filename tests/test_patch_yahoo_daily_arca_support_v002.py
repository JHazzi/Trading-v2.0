from tools.patch_yahoo_daily_arca_support_v002 import patch_text, inspect_source


BASE = """\
EXCHANGE_CANONICAL_MAP = {
    "XNYS": "XNYS",
    "NYSE": "XNYS",
    "XNAS": "XNAS",
    "NASDAQ": "XNAS",
    "BATS": "BATS",
    "BZX": "BATS",
}
EXCHANGE_CALENDAR_MAP = {
    "XNYS": "XNYS",
    "XNAS": "XNAS",
    "BATS": "XNYS",
}
OTHER = 1
"""


def test_handles_preexisting_extra_exchange_aliases():
    patched, info = patch_text(BASE)
    assert info["status"] == "already_applied"
    assert '"BATS": "BATS"' in patched
    assert "'ARCX': 'ARCX'" in patched
    assert "'NYSE ARCA': 'ARCX'" in patched
    assert "'ARCX': 'XNYS'" in patched
    assert "OTHER = 1" in patched


def test_is_idempotent():
    once, _ = patch_text(BASE)
    twice, info = patch_text(once)
    assert once == twice
    assert info["status"] == "already_applied"


def test_preserves_existing_comments_and_code():
    text = """\
# before
EXCHANGE_CANONICAL_MAP = {
    "XNYS": "XNYS",  # keep
    "XNAS": "XNAS",
}
# middle
EXCHANGE_CALENDAR_MAP = {
    "XNYS": "XNYS",
    "XNAS": "XNAS",
}
# after
"""
    patched, _ = patch_text(text)
    assert '"XNYS": "XNYS",  # keep' in patched
    assert "# middle" in patched
    assert "# after" in patched


def test_detects_conflicting_arca_mapping():
    text = """\
EXCHANGE_CANONICAL_MAP = {
    "XNYS": "XNYS",
    "XNAS": "XNAS",
    "ARCX": "WRONG",
}
EXCHANGE_CALENDAR_MAP = {
    "XNYS": "XNYS",
    "XNAS": "XNAS",
}
"""
    info = inspect_source(text)
    assert info["status"] == "conflict"
    assert info["canonical_conflicts"]["ARCX"] == "WRONG"

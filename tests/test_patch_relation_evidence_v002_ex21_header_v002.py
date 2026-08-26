from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
PATCHER = ROOT / "tools" / "patch_relation_evidence_v002_ex21_header_v002.py"

def load_module():
    spec = importlib.util.spec_from_file_location("patcher_v002", PATCHER)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    spec.loader.exec_module(module)
    return module

BASE = 'BAD_NAME_PHRASES = re.compile(\n    r"""\n    (?:\n      \\btable\\s+of\\s+contents\\b\n      |\\barticle\\s+\\d+\\b\n      |\\bsection\\s+\\d\n      |\\brepresentations\\s+and\\s+warranties\\b\n      |\\bwhere\\s+incorporated\\b\n      |\\bplace\\s+of\\s+incorporation\\b\n      |\\bstate\\s+or\\s+country\\b\n      |\\bjurisdiction\\s+of\\b\n      |\\bshareholder\\s+register\\b\n      |\\bboard\\s+approvals?\\b\n      |\\bconduct\\s+of\\s+business\\b\n      |\\bclosing,\\s+the\\s+company\\b\n      |\\bmerger,\\s+the\\s+company\\b\n      |\\bacquiror,\\s+the\\s+company\\b\n    )\n    """,\n    re.I | re.X,\n)\n\ndef quality_flags(name: str):\n    return ()\n'

def test_check_recognizes_real_v002_structure():
    m = load_module()
    out = m.inspect_source(BASE)
    assert out["status"] == "PASS"
    assert out["would_apply"] is True
    assert out["already_applied"] is False

def test_patch_adds_rule_in_bad_name_block():
    m = load_module()
    patched = m.patch_text(BASE)
    assert r"|\borganized\s+or\s+incorporated\b" in patched
    out = m.inspect_source(patched)
    assert out["status"] == "PASS"
    assert out["already_applied"] is True

def test_patch_is_idempotent():
    m = load_module()
    once = m.patch_text(BASE)
    twice = m.patch_text(once)
    assert once == twice
    assert once.count(r"\borganized\s+or\s+incorporated\b") == 1

def test_refuses_unknown_structure():
    m = load_module()
    out = m.inspect_source("def quality_flags(name): return ()")
    assert out["status"] == "FAIL"

def test_refuses_missing_anchor():
    m = load_module()
    text = BASE.replace(
        r"|\bwhere\s+incorporated\b",
        r"|\bwhere\s+formed\b",
    )
    out = m.inspect_source(text)
    assert out["status"] == "FAIL"

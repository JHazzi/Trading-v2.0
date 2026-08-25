from pathlib import Path

def test_t0_patcher_mentions_first_public_and_sec():
    source = Path("tools/patch_event_t0_docs_v001.py").read_text(
        encoding="utf-8"
    )
    assert "first_public_at" in source
    assert "SEC accepted_at == first_public_at == event_time" in source
    assert "D021" in source

def test_patcher_is_idempotent_by_marker():
    source = Path("tools/patch_event_t0_docs_v001.py").read_text(
        encoding="utf-8"
    )
    assert "EVENT_T0_V001_START" in source
    assert "already_applied" in source

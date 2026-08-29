from pathlib import Path
import sqlite3

from research.news.legacy_corpus_audit_v001 import audit, normalize_title


def test_title_normalization():
    assert normalize_title("Reuters: Apple  launches!!!  product") == "apple launches product"


def test_audit_blocks_naive_time_alignment(tmp_path):
    db = tmp_path/"x.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE noticias(id INTEGER, ticker TEXT, timestamp TEXT, titulo TEXT, fuente TEXT, resumen TEXT, sentimiento REAL, url TEXT)")
    c.execute("CREATE TABLE precios(ticker TEXT, timestamp TEXT, close REAL)")
    c.executemany("INSERT INTO noticias VALUES(?,?,?,?,?,?,?,?)", [
        (1,"AAPL","2026-08-27 14:03:00","Rocket event","Reuters","x",-0.3,"https://x/a"),
        (2,"AAPL","2026-08-27 14:05:00","Rocket event","Yahoo","x",-0.2,"https://x/b"),
    ])
    c.executemany("INSERT INTO precios VALUES(?,?,?)", [
        ("AAPL","2026-08-27 14:02:00",100.0),
        ("AAPL","2026-08-27 14:03:00",90.0),
        ("AAPL","2026-08-27 14:04:00",88.0),
    ])
    c.commit(); c.close()
    out = audit(db)
    assert out["news"]["rows"] == 2
    assert out["news"]["dedup_diagnostics"]["exact_normalized_title_duplicate_rows"] == 1
    assert out["reaction_alignment_gate"] == "BLOCKED_NEEDS_NEWS_TIMEZONE_CONTRACT"


def test_aware_times_can_reach_descriptive_gate(tmp_path):
    db = tmp_path/"y.db"
    c = sqlite3.connect(db)
    c.execute("CREATE TABLE noticias(id INTEGER, ticker TEXT, timestamp TEXT, titulo TEXT, fuente TEXT)")
    c.execute("CREATE TABLE precios(ticker TEXT, timestamp TEXT, close REAL)")
    c.executemany("INSERT INTO noticias VALUES(?,?,?,?,?)", [
        (1,"AAPL","2026-08-27T14:03:00+00:00","A","Reuters"),
        (2,"MSFT","2026-08-27T14:04:00+00:00","B","AP"),
    ])
    c.executemany("INSERT INTO precios VALUES(?,?,?)", [
        ("AAPL","2026-08-27T14:02:00+00:00",100.0),
        ("AAPL","2026-08-27T14:03:00+00:00",101.0),
        ("AAPL","2026-08-27T14:04:00+00:00",102.0),
        ("MSFT","2026-08-27T14:02:00+00:00",200.0),
        ("MSFT","2026-08-27T14:03:00+00:00",201.0),
        ("MSFT","2026-08-27T14:04:00+00:00",202.0),
    ])
    c.commit(); c.close()
    out = audit(db)
    assert out["reaction_alignment_gate"] == "READY_FOR_DESCRIPTIVE_ALIGNMENT_ONLY"

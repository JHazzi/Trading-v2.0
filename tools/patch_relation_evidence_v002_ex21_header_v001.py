from __future__ import annotations
import argparse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
TARGET=ROOT/"knowledge/relations/relation_evidence_extraction_v002.py"
PHRASE='r"\\bwhere\\s+incorporated\\b"'
NEW='r"\\bwhere\\s+incorporated\\b"\n      r"|\\borganized\\s+or\\s+incorporated\\b"'

def main():
    p=argparse.ArgumentParser()
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check",action="store_true")
    g.add_argument("--apply",action="store_true")
    a=p.parse_args()
    if not TARGET.is_file():
        raise FileNotFoundError(TARGET)
    text=TARGET.read_text()
    if "organized\\\\s+or\\\\s+incorporated" in text:
        print("already_applied")
        return
    if PHRASE not in text:
        raise RuntimeError("expected V002 header guard anchor not found")
    if a.check:
        print("would_apply")
        return
    TARGET.write_text(text.replace(PHRASE,NEW,1))
    print("applied")
if __name__=="__main__":main()

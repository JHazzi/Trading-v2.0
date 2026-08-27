from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
KEY = "MARKET_DIST_V007_ZERO_VOL_AMENDMENT_V0011"
BODY = """## V007 pre-performance implementation amendment — exact zero volatility

The first V007 H1 benchmark attempt aborted during data loading before any OOS metric was produced. Core V003 permits exact zero rolling volatility, while the initial V007 loader incorrectly required both vol20 and vol63 to be strictly positive.

V007.0.1 corrects only this domain handling. Exact observed zero volatility is mapped to the already-frozen lower log-ratio clip; a nonpositive per-asset TRAIN median normalizer falls back to the positive global TRAIN median. The vol20 control restores the completed V006 `global_empirical_fallback` behavior for nonpositive scale rows, and vol63 uses the same prospective control rule. Negative/null volatility remains a hard error.

No rows are dropped, no epsilon is introduced, and no alpha/lambda/kappa grid, feature, primary reference, quantile, horizon, fold or score is changed. The plan gate now reports zero/negative/null scale support from the real Core V003 DB. This amendment must be committed before rerunning V007.
"""
FILES = [
    ROOT / "docs" / "EXPERIMENTS.md",
    ROOT / "docs" / "RESEARCH_STATUS.md",
    ROOT / "docs" / "RESEARCH_DECISIONS.md",
    ROOT / "docs" / "ROADMAP.md",
]

def marker(suffix: str) -> str:
    return f"<!-- {KEY}_{suffix} -->"

def upsert(text: str) -> str:
    start, end = marker("START"), marker("END")
    block = f"{start}\n{BODY.rstrip()}\n{end}"
    if start in text and end in text:
        left = text.index(start); right = text.index(end, left) + len(end)
        return text[:left] + block + text[right:]
    return text.rstrip() + "\n\n" + block + "\n"

def main() -> None:
    p=argparse.ArgumentParser(); g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true"); g.add_argument("--apply", action="store_true")
    a=p.parse_args(); changed=[]
    for path in FILES:
        if not path.exists(): raise FileNotFoundError(path)
        old=path.read_text(encoding="utf-8"); new=upsert(old)
        if old != new:
            changed.append(str(path.relative_to(ROOT)))
            if a.apply: path.write_text(new, encoding="utf-8")
    print("mode:", "apply" if a.apply else "check")
    print("files_changed:", len(changed))
    for item in changed: print(" -", item)

if __name__ == "__main__": main()

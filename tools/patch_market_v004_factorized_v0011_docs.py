from __future__ import annotations
import argparse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MARKER="<!-- MARKET_V004_FACTORIZED_V0011_COVERAGE_FIX -->"
BLOCK=r"""

<!-- MARKET_V004_FACTORIZED_V0011_COVERAGE_FIX -->
## Market V004 factorized benchmark V001.1 coverage correction

V001 was stopped at the plan gate before model results.

The plan revealed that the additive primary was accidentally restricted to
rows with finite dynamic beta/gamma features (~85.7% coverage). That violated
the preregistered contract: additive is the broad-coverage primary, dynamic
beta/gamma is secondary.

V001.1 separates additive and dynamic feature availability and hard-fails its
plan unless additive coverage includes all V003 OOS state rows and at least
98% of usable V004 target rows.

No V001 factorized model performance was observed before this correction.
"""


def main():
    p=argparse.ArgumentParser()
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check",action="store_true")
    g.add_argument("--apply",action="store_true")
    a=p.parse_args()
    for rel in ("docs/EXPERIMENTS.md","docs/RESEARCH_STATUS.md"):
        path=ROOT/rel
        if not path.is_file(): raise FileNotFoundError(path)
        x=path.read_text(encoding="utf-8")
        if MARKER in x:
            print("already_applied",rel)
        elif a.apply:
            path.write_text(x.rstrip()+"\n"+BLOCK.lstrip(),encoding="utf-8")
            print("applied",rel)
        else:
            print("would_apply",rel)

if __name__=="__main__":
    main()

from __future__ import annotations
import argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MARKER="<!-- MARKET_V004_RESULTS_V005_EXTERNAL_STATE -->"
BLOCK=r"""

<!-- MARKET_V004_RESULTS_V005_EXTERNAL_STATE -->
## Market V004 factorized result and V005 external-state decision

V004 factorization materially improves V003 but does not pass the preregistered
absolute-return primary against fold train median at H1/H3/H5/H10. The loss
survives moving-block bootstrap.

Decision:

- retain V003 and V004 as canonical evidence;
- stop additional endogenous-price factorization as the primary research path;
- begin V005 incremental Market State enrichment;
- first increment is SPY/QQQ/IWM only;
- sector ETFs, volatility/rates/credit, macro, events and distributional heads
  remain separate later increments;
- no post-result tuning of V004.

This remains Architecture Phase C: improve the base Market Brain without news
before Event Brain integration.
"""
def main():
    p=argparse.ArgumentParser(); g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check",action="store_true"); g.add_argument("--apply",action="store_true")
    a=p.parse_args()
    for rel in ("docs/RESEARCH_STATUS.md","docs/RESEARCH_DECISIONS.md","docs/ROADMAP.md"):
        path=ROOT/rel
        if not path.exists(): raise FileNotFoundError(path)
        x=path.read_text()
        if MARKER in x: print("already_applied",rel)
        elif a.apply:
            path.write_text(x.rstrip()+"\n"+BLOCK.lstrip()); print("applied",rel)
        else: print("would_apply",rel)
if __name__=="__main__": main()

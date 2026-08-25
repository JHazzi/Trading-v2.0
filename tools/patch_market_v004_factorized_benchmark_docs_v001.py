from __future__ import annotations
import argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MARKER="<!-- MARKET_V004_FACTORIZED_BENCHMARK_V001 -->"

BLOCK=r"""

<!-- MARKET_V004_FACTORIZED_BENCHMARK_V001 -->
## Market Brain Daily V004 factorized benchmark — preregistration

The V004 mathematical dataset passed its identity/coverage audit. No
predictability claim follows from that audit.

Primary experiment:

```text
predict market factor once/day
+ predict sector residual once/sector-day
+ predict asset residual once/asset-day
= reconstructed absolute asset return
```

Primary candidate: fixed HGB additive reconstruction.
Primary baseline: the exact V003 fold-specific train median prediction on the
same OOS state rows.

Secondary references:

```text
V003 HGB full
Ridge factorized reconstruction
dynamic beta/gamma reconstruction
```

Dynamic beta is secondary because its ~85.7% coverage creates a restricted
comparison subset.

The outer test boundaries are inherited exactly from Benchmark V001.1.
Every component training row must have `target_end_day < first_test_day`.

No proxy data, macro, events, distributional heads or post-result tuning enter
this benchmark.
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
if __name__=="__main__": main()

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- MARKET_V005_MARKET_TRADABLES_BENCHMARK_V001 -->"

BLOCK = r"""

<!-- MARKET_V005_MARKET_TRADABLES_BENCHMARK_V001 -->
## Market Brain Daily V005 market-tradables benchmark — preregistration

V004 factorization remains the frozen control. V005 tests one information
increment only: SPY/QQQ/IWM market state available at the origin-session close.

Primary comparison:

```text
V004 additive HGB reconstruction
vs
V005 additive HGB reconstruction
```

Only the market-level model receives new features. Sector and asset models,
targets, folds, hyperparameters and OOS state rows are unchanged.

The new market block contains 22 features derived from SPY/QQQ/IWM.
Historical Yahoo reference data uses the documented historical-session-close
assumption and is not strict provider point-in-time replay.

Two conclusions are kept separate:

1. incremental information value: V005 vs V004;
2. absolute skill checkpoint: V005 vs fold train median.

An external block can be retained for the next information stage if its paired
moving-block bootstrap improvement over V004 is positive across the
preregistered 5/10/20 origin-day blocks. This does not by itself imply absolute
market-prediction skill.

No sector ETFs, VIX, rates/credit, macro, events, distributional heads or
hyperparameter search enter this benchmark.
"""


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = p.parse_args()

    for rel in ("docs/EXPERIMENTS.md", "docs/RESEARCH_STATUS.md"):
        path = ROOT / rel
        if not path.is_file():
            raise FileNotFoundError(path)
        x = path.read_text(encoding="utf-8")
        if MARKER in x:
            print("already_applied", rel)
        elif a.apply:
            path.write_text(
                x.rstrip() + "\n" + BLOCK.lstrip(),
                encoding="utf-8",
            )
            print("applied", rel)
        else:
            print("would_apply", rel)


if __name__ == "__main__":
    main()

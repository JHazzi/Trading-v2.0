from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- MARKET_V0052_FINANCIAL_CONDITIONS_V001 -->"
BLOCK = r'''

<!-- MARKET_V0052_FINANCIAL_CONDITIONS_V001 -->
## Market Brain V005.2 — financial conditions

V005.1 SPY/QQQ/IWM did not pass its preregistered incremental gate over V004.
It is retained as negative/inconclusive evidence and is not stacked into the
next primary candidate.

V005.2 tests a more orthogonal Market State block:

```text
volatility: Cboe VIX (previous session only)
rates:      SHY / IEF / TLT
credit:     HYG / LQD
```

The V004 factorized Market Brain remains the frozen control.

Causal clock:
- same-day ETF closes are available at the equity-session origin;
- same-day daily VIX close is NOT used because Cboe VIX RTH continues after
  the equity close; VIX features use one full session lag;
- historical reference data remains research backfill, strict PIT=false;
- Yahoo adjusted_close is not used for ETF state features;
- ETF returns are reconstructed from Close plus only cash distributions whose
  effective trading day has occurred, then compounded over 5/20 sessions.

Primary candidate is the complete financial-conditions block. VIX-only,
rates-only and credit-only candidates are preregistered diagnostics and cannot
rescue a failed primary after results.

No sector ETFs, macro vintages, Event Brain, graph, distributional heads,
regime-conditioned training or hyperparameter tuning enter V005.2.
'''


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = p.parse_args()

    for rel in (
        "docs/RESEARCH_STATUS.md", "docs/RESEARCH_DECISIONS.md",
        "docs/ROADMAP.md", "docs/EXPERIMENTS.md",
    ):
        path = ROOT / rel
        if not path.is_file():
            raise FileNotFoundError(path)
        text = path.read_text(encoding="utf-8")
        if MARKER in text:
            print("already_applied", rel)
        elif a.apply:
            path.write_text(text.rstrip() + "\n" + BLOCK.lstrip(), encoding="utf-8")
            print("applied", rel)
        else:
            print("would_apply", rel)


if __name__ == "__main__":
    main()

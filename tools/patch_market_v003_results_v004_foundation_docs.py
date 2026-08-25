from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- MARKET_V003_RESULTS_V004_FACTORIZATION_V001 -->"

STATUS = r"""

<!-- MARKET_V003_RESULTS_V004_FACTORIZATION_V001 -->
## Market Daily V003 Benchmark V001.1 — closed

The preregistered primary claim was rejected at every horizon.

Primary metric is:

```text
train_median MAE - HGB_full MAE
```

so positive is better for HGB.

Observed pooled deltas:

```text
H1   -0.0484 pp
H3   -0.2945 pp
H5   -0.7561 pp
H10  -1.0025 pp
```

All 5/10/20-origin-day moving-block bootstrap confidence intervals remain
strictly below zero at every horizon.

The dominant nonlinear degradation is the transition:

```text
HGB own -> HGB own + cross-section
```

which is negative at H1/H3/H5/H10. Sector context is smaller and mixed but
does not rescue the full model.

Therefore Market V003 is not promoted to distributional modeling and is not
used as the base for Event Brain integration.
"""

DECISION = r"""

<!-- MARKET_V003_RESULTS_V004_FACTORIZATION_V001 -->
## D022 — Factorize Market Brain before adding more context

Market V003 demonstrated that pooling own-asset, market-day and sector-day
signals into one asset-day nonlinear model is not currently robust.

Next architecture hypothesis:

```text
Market factor model
    unit = day
        +
Sector residual model
    unit = sector-day
        +
Asset residual model
    unit = asset-day
```

with exact target identity:

```text
asset return
= market factor
+ sector factor
+ asset residual
```

This is a hypothesis to test, not an established explanation of the V003
failure.

Before training V004:

1. quantify common-market and sector target variance;
2. quantify feature replication/topology by statistical unit;
3. freeze the factorized target contract;
4. materialize each level separately;
5. benchmark each component before recombination.

Do not yet add external market proxies, macro, Event Brain, distributional
outputs, or tune V003 after observing its benchmark.
"""

ROADMAP = r"""

<!-- MARKET_V003_RESULTS_V004_FACTORIZATION_V001 -->
### Phase 2 next step — Market Daily V004 factorization

V003 absolute-return benchmark is scientifically closed as a negative result.

Current sequence:

```text
V003 Core / Broad Panel                         DONE
V003 preregistered benchmark                    DONE
V003 primary absolute-return hypothesis        REJECTED
V004 factorization postmortem                  NEXT
V004 market/sector/asset component datasets    after postmortem
V004 component benchmarks                      after dataset audit
external proxies                               later incremental test
distributional Market Brain                    after stable point baseline
Event Brain integration                        after stable Market Brain
```
"""


def append_once(path: Path, block: str, apply: bool) -> str:
    content = path.read_text(encoding="utf-8")
    if MARKER in content:
        return "already_applied"
    if apply:
        path.write_text(
            content.rstrip() + "\n" + block.lstrip(),
            encoding="utf-8",
        )
        return "applied"
    return "would_apply"


def main() -> None:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = p.parse_args()

    for rel, block in (
        ("docs/RESEARCH_STATUS.md", STATUS),
        ("docs/RESEARCH_DECISIONS.md", DECISION),
        ("docs/ROADMAP.md", ROADMAP),
    ):
        path = ROOT / rel
        if not path.is_file():
            raise FileNotFoundError(path)
        print(f"{append_once(path, block, args.apply):15s} {rel}")


if __name__ == "__main__":
    main()

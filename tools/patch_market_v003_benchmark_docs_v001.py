from __future__ import annotations
import argparse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MARKER="<!-- MARKET_V003_BENCHMARK_V001 -->"

STATUS=r"""

<!-- MARKET_V003_BENCHMARK_V001 -->
## Market Daily V003 Core gate passed

Corrected Core audit:

```text
status PASS
1,092,555 states
497 assets
1,078,329 usable H1 labels
1,049,926 usable H3 labels
1,021,619 usable H5 labels
951,231 usable H10 labels
```

Corporate-action overlap fraction:

```text
H1   1.26%
H3   3.77%
H5   6.27%
H10 12.48%
```

The raw-close label contract is retained for the preregistered V003 benchmark;
it is interpreted as a no-corporate-action-overlap research target, not a
production total-return target.

Next: frozen Market Daily V003 Benchmark V001.
"""

EXPERIMENTS=r"""

<!-- MARKET_V003_BENCHMARK_V001 -->
## Market Brain Daily V003 Benchmark V001 — preregistration

Primary comparison:

```text
train median vs HGB full market state
```

Five purged expanding temporal folds, initial 30% training history. Training
rows satisfy:

```text
target_trading_day < first_test_origin_day
```

Models are fixed before results:

```text
zero
train mean
train median
asset train mean
same-horizon momentum

Ridge full
SGD Huber full

HistGradientBoosting own-only
HistGradientBoosting own + cross-section
HistGradientBoosting full (+ sector)
```

No hyperparameter tuning and no best-model selection for the primary claim.

Inference uses paired daily losses and moving-block bootstrap on origin days
(5/10/20). Row-level iid confidence intervals are not used.

Random Forest is intentionally deferred to robustness because the broad panel
contains roughly one million rows per horizon; it is not needed to establish
the first nonlinear benchmark.
"""

ROADMAP=r"""

<!-- MARKET_V003_BENCHMARK_V001 -->
### Market Daily V003 Benchmark

Run H1/H3/H5/H10 independently under the frozen benchmark plan.

Decision after results:

- if full Market V003 cannot consistently beat train-median / asset-mean
  baselines, improve market representation before Event Brain integration;
- if own-only works but cross-section/sector do not add value, do not keep
  context merely because it is architecturally appealing;
- if cross-section or sector adds paired OOS value, retain only the supported
  layers;
- only after this benchmark consider SPY/QQQ/IWM, volatility/rates, RF
  robustness, or distributional outputs.
"""


def append(path, block, apply):
    content=path.read_text(encoding="utf-8")
    if MARKER in content: return "already_applied"
    if apply:
        path.write_text(content.rstrip()+"\n"+block.lstrip(),encoding="utf-8")
        return "applied"
    return "would_apply"


def main():
    p=argparse.ArgumentParser()
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check",action="store_true")
    g.add_argument("--apply",action="store_true")
    a=p.parse_args()
    for rel,block in (
        ("docs/RESEARCH_STATUS.md",STATUS),
        ("docs/EXPERIMENTS.md",EXPERIMENTS),
        ("docs/ROADMAP.md",ROADMAP),
    ):
        path=ROOT/rel
        if not path.is_file(): raise FileNotFoundError(path)
        print(f"{append(path,block,a.apply):15s} {rel}")


if __name__=="__main__":
    main()

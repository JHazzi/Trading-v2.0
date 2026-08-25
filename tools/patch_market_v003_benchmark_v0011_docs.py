from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- MARKET_V003_BENCHMARK_V0011 -->"

EXPERIMENT = r"""

<!-- MARKET_V003_BENCHMARK_V0011 -->
## Market Brain Daily V003 Benchmark V001.1 — supersedes V001 before results

No V001 model performance was observed before this hardening.

Primary scientific design is unchanged:

```text
all eligible asset-days
H1 / H3 / H5 / H10
5 purged expanding temporal folds
initial_fraction = 0.30
primary = train median vs HGB full
moving-block bootstrap by origin day
```

Pre-result hardening:

```text
+ asset train median baseline
+ always-up/down/train-majority direction baselines
+ HGB early_stopping=False explicitly
+ visible latest train target / purge row counts per fold
+ git/environment/code/Core-DB SHA256 preregistration
```

V001 reports remain historical and V001.1 writes to `benchmark_v0011/`.
"""

STATUS = r"""

<!-- MARKET_V003_BENCHMARK_V0011 -->
## Active Market Daily V003 benchmark

Active preregistration: `market_brain_daily_v003_benchmark_v0011`.

V001 was superseded before any model performance was observed. The Core
dataset, horizons, 30% initial training fraction, five-fold purged
walk-forward, primary model and primary MAE comparison are unchanged.

The change only strengthens baselines, makes HGB stopping behavior explicit,
exposes purge boundaries, and freezes code/data/environment hashes.
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
        ("docs/EXPERIMENTS.md", EXPERIMENT),
        ("docs/RESEARCH_STATUS.md", STATUS),
    ):
        path = ROOT / rel
        if not path.is_file():
            raise FileNotFoundError(path)
        print(f"{append_once(path, block, args.apply):15s} {rel}")


if __name__ == "__main__":
    main()

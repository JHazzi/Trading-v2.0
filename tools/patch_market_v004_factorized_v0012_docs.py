from __future__ import annotations
import argparse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MARKER="<!-- MARKET_V004_FACTORIZED_V0012_DUPLICATE_EXPOSURE_FIX -->"
BLOCK=r"""

<!-- MARKET_V004_FACTORIZED_V0012_DUPLICATE_EXPOSURE_FIX -->
## Market V004 factorized benchmark V001.2 duplicate-exposure correction

V001.1 also stopped at the plan gate before model results.

Root cause of persistent ~85.7% additive coverage: beta/gamma exposures existed
both in `v004_factor_targets` and `v004_asset_states`. The modeling merge kept
the state copies with `_state` suffixes. Those aliases were not excluded by
the V001.1 dynamic-feature filter, so they still gated the additive primary.

V001.2 makes `v004_asset_states` the canonical source of exposure features,
drops exposure copies from the target table before the merge, and hard-fails
if suffixed exposure aliases survive.

The plan additionally requires:

```text
additive_asset_rows == raw_usable_asset_rows
all V003 OOS states included
20 additive asset features
25 dynamic asset features
dynamic subset strictly smaller than additive
```

No V004 factorized model performance was observed before this correction.
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

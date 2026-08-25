from __future__ import annotations
import argparse
from pathlib import Path
ROOT=Path(__file__).resolve().parents[1]
MARKER='<!-- MARKET_V003_CORE_DATASET_V001 -->'
STATUS='''
<!-- MARKET_V003_CORE_DATASET_V001 -->
## Market Daily V003 broad backfill closed

Broad current-cohort backfill result:

```text
493 planned backfills
490 completed
3 quality-quarantined: FISV, HUBB, MNST
500 assets with quality-gated daily data
497 assets ready for >=253-session state
489 assets with >=1260 daily sessions
```

The three failures each downloaded the requested history but failed a strict
single-row quality condition. They remain quarantined rather than weakening
the gate. The >=300 / >=300 broad-panel readiness gate is passed.

Next scientific stage is the deterministic Market Daily V003 Core Dataset:
all eligible asset-days + own state + leave-one-out market/sector context +
separate 1/3/5/10-session labels. External proxies, macro and event features
remain deferred.
'''
ROADMAP='''
<!-- MARKET_V003_CORE_DATASET_V001 -->
### Broad panel gate passed

Do not backfill more equities merely to obtain 503/503. Three assets remain
quality-quarantined while 500 clean assets provide a panel well above the
predeclared readiness gate.

Before model training:
1. materialize deterministic Market V003 core states;
2. materialize future labels separately from features;
3. audit leakage/coverage/sector missingness;
4. quantify corporate-action exclusion by horizon;
5. only then freeze the benchmark battery.

If H10 corporate-action exclusion is large, do not silently accept the
selection bias; evaluate a causally-defined total-return label version later.
'''

def append_once(path,block,apply):
    content=path.read_text(encoding='utf-8')
    if MARKER in content:return 'already_applied'
    if apply:path.write_text(content.rstrip()+'\n'+block.lstrip(),encoding='utf-8');return 'applied'
    return 'would_apply'

def main():
    p=argparse.ArgumentParser();g=p.add_mutually_exclusive_group(required=True);g.add_argument('--check',action='store_true');g.add_argument('--apply',action='store_true');a=p.parse_args()
    for rel,block in (("docs/RESEARCH_STATUS.md",STATUS),("docs/ROADMAP.md",ROADMAP)):
        path=ROOT/rel
        if not path.is_file():raise FileNotFoundError(path)
        print(f"{append_once(path,block,a.apply):15s} {rel}")
if __name__=='__main__':main()

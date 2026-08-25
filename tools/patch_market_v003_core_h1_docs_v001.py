from __future__ import annotations
import argparse
from pathlib import Path

ROOT=Path(__file__).resolve().parents[1]
MARKER="<!-- MARKET_V003_CORE_H1_FIX_V001 -->"
BLOCK=r"""

<!-- MARKET_V003_CORE_H1_FIX_V001 -->
## Market Daily V003 Core H1 label correction

The first Core audit was superseded because it allowed all H1 labels to be
`insufficient_future`.

Cause: H1 path volatility used sample std (`ddof=1`) on a one-return path.

Decision:

```text
path volatility := population std (ddof=0)
H1 path volatility := 0
```

The audit now treats missing/substantially unusable horizons as hard failures.
The processed Core DB is rebuilt from source observations rather than patched
in place.
"""


def main():
    p=argparse.ArgumentParser()
    g=p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check",action="store_true")
    g.add_argument("--apply",action="store_true")
    a=p.parse_args()
    for rel in ("docs/RESEARCH_STATUS.md","docs/RESEARCH_DECISIONS.md"):
        path=ROOT/rel
        if not path.is_file(): raise FileNotFoundError(path)
        content=path.read_text(encoding="utf-8")
        if MARKER in content:
            print(f"already_applied {rel}")
        elif a.apply:
            path.write_text(content.rstrip()+"\n"+BLOCK.lstrip(),encoding="utf-8")
            print(f"applied         {rel}")
        else:
            print(f"would_apply     {rel}")


if __name__=="__main__":
    main()

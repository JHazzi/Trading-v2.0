from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- EVENT_GRAPH_BRAIN_FOUNDATION_V001 -->"

BLOCK = r"""

<!-- EVENT_GRAPH_BRAIN_FOUNDATION_V001 -->
## Event–Graph Brain Foundation V001

Market Brain V004 is retained as the frozen structural prior/control. V005.1
and V005.2 remain evidence about market-context information and are not stacked
into Event–Graph Brain.

The next architecture work resumes phases D/E:

```text
evidence -> event -> entity
relation evidence -> temporal structural graph
event + G_t -> asset exposure candidates
```

New canonical contract: `docs/EVENT_GRAPH_CONTRACTS.md`.

Foundation rules:

- candidate extraction is not model-visible until resolution/promotion;
- structural relation evidence must satisfy `available_at <= t`;
- graph propagation nominates potentially exposed assets but assigns no market
  direction or predictive weight;
- structural graph is first; statistical/learned graph and GNN are deferred;
- foundation propagation is one hop;
- evaluation is nested:
  `V004+direct event vs V004`, then
  `V004+direct event+graph vs V004+direct event`;
- graph claims require negative controls, including matched unconnected assets
  and future-evidence leakage checks.

No Event–Graph predictive model is trained in the foundation package.
"""


def main():
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    a = p.parse_args()

    targets = (
        "docs/INDEX.md",
        "docs/RESEARCH_STATUS.md",
        "docs/RESEARCH_DECISIONS.md",
        "docs/ROADMAP.md",
        "docs/EXPERIMENTS.md",
    )
    for rel in targets:
        path = ROOT / rel
        if not path.is_file():
            raise FileNotFoundError(path)
        text = path.read_text(encoding="utf-8")
        if MARKER in text:
            print("already_applied", rel)
        elif a.apply:
            path.write_text(
                text.rstrip() + "\n" + BLOCK.lstrip(),
                encoding="utf-8",
            )
            print("applied", rel)
        else:
            print("would_apply", rel)


if __name__ == "__main__":
    main()

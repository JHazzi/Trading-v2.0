from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- MARKET_V003_BROAD_BACKFILL_V001 -->"

STATUS_APPEND = r"""

<!-- MARKET_V003_BROAD_BACKFILL_V001 -->
## Market Daily V003 foundation audit — 2026-08-25

Foundation audit result:

```text
active equities                       503
assets with quality-gated daily data   10
assets >= 1,260 daily sessions         10
assets >= 2,000 daily sessions         10
latest assets ready for 253-day state  10
strict historical PIT rows              0
```

No day currently reaches the minimum 50-asset cross-section gate.

Decision:

```text
BROAD_PANEL_BACKFILL_REQUIRED
```

The next data step is a listing-aware daily Yahoo backfill for the existing
503-equity current research cohort. Assets enter the model dynamically after
sufficient own history; they are not forced into a common historical start.

This cohort is explicitly **not survivorship-free historical index
membership**. It is a current-asset historical research cohort.

SPY/QQQ/IWM, sector ETFs, volatility/rate/credit proxies are absent and are
deferred until after the core broad-equity panel is audited.

Legacy macro observations remain excluded because no causal
release/vintage/availability contract exists.
"""

ROADMAP_APPEND = r"""

<!-- MARKET_V003_BROAD_BACKFILL_V001 -->
## Market Daily V003 broad panel gate — 2026-08-25

Current gate:

```text
503 active equities known
10 quality-gated daily histories
BROAD_PANEL_BACKFILL_REQUIRED
```

Execution order:

1. discover per-asset first available Yahoo daily session and exchange;
2. audit the discovery manifest;
3. ingest a five-asset smoke batch through the existing append-only daily
   causal ingestion;
4. audit the smoke;
5. resume the full current-cohort backfill;
6. require a broad-panel audit before feature construction/training;
7. build Market V003 core all-asset-day features without external proxies;
8. test broad-market/sector/rate/volatility proxies later as incremental
   context, not as prerequisites.

Macro remains out until causal vintages exist.
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

    targets = [
        ("docs/RESEARCH_STATUS.md", STATUS_APPEND),
        ("docs/ROADMAP.md", ROADMAP_APPEND),
    ]
    for rel, block in targets:
        path = ROOT / rel
        if not path.is_file():
            raise FileNotFoundError(path)
        print(f"{append_once(path, block, args.apply):15s} {rel}")


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

BLOCKS = {
    "docs/RESEARCH_STATUS.md": """
<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_START -->
## Expectation / Information Capture Foundation V001 — parallel data asset

While Market Distributional V009 remains frozen in prospective holdout, a separate append-only information-capture database may accumulate strict-PIT observations of scheduled events, expectations/guidance and later reported economic facts. This foundation is **not model-visible**, does not modify V009, and makes no predictive claim. Historical backfills remain `strict_pit=0`; only genuinely observed live evidence may be `strict_pit=1` under the capture contract.
<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_END -->
""",
    "docs/RESEARCH_DECISIONS.md": """
<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_START -->
## Decision — accumulate future information vintages without contaminating V009

**Decision:** permit a parallel append-only Expectation / Information Capture Foundation in a database separate from Market Core. It may collect live strict-PIT evidence while V009 accumulates, but none of its records are model-visible or allowed to validate, rescue, refit or modify V009. Historical expectation backfills must remain explicitly non-strict-PIT.

**Reason:** beliefs, revisions, guidance, scheduled uncertainty and actual-vs-expectation surprise are scientifically important but are difficult to reconstruct faithfully after the fact. Capturing them prospectively creates future research data without changing the frozen Market Brain experiment.
<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_END -->
""",
    "docs/ROADMAP.md": """
<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_START -->
## Parallel track — strict-PIT expectation/information capture

This track may run while V009 accumulates because it is data-only and isolated from V009. Order:

```text
capture contract + isolated DB           ACTIVE
provider/source semantic audit           NEXT
prospective scheduled/expectation capture AFTER provider contract
feature derivation                        BLOCKED until preregistered experiment
predictive use                            BLOCKED until incremental Event/Information gate
```

No provider is promoted by convenience, no historical backfill is relabeled strict PIT, and no captured field enters a predictor without a later preregistered incremental-information experiment.
<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_END -->
""",
    "docs/EXPERIMENTS.md": """
<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_START -->
## DATA-EXPECTATION-CAPTURE-V001 — prospective information vintage foundation

**Type:** data foundation, not predictive experiment.

**Purpose:** persist immutable, causally timestamped source observations, scheduled-event revisions, expectation/guidance snapshots and reported economic facts in a database isolated from V009/Market Core.

**Claim boundary:** infrastructure/capture lineage only. No alpha, information-value or model-performance claim is permitted.
<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_END -->
""",
}


def apply_file(path: Path, block: str, do_apply: bool) -> tuple[str, str]:
    if not path.exists():
        return str(path), "MISSING"
    text = path.read_text(encoding="utf-8")
    marker = block.strip().splitlines()[0]
    if marker in text:
        return str(path), "ALREADY_PRESENT"
    if do_apply:
        path.write_text(text.rstrip() + "\n\n" + block.strip() + "\n", encoding="utf-8")
        return str(path), "APPLIED"
    return str(path), "WOULD_APPLY"


def main() -> None:
    ap = argparse.ArgumentParser()
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--check", action="store_true")
    mode.add_argument("--apply", action="store_true")
    args = ap.parse_args()
    for rel, block in BLOCKS.items():
        path, status = apply_file(ROOT / rel, block, args.apply)
        print(f"{status}: {path}")


if __name__ == "__main__":
    main()

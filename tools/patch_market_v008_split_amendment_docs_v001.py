from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "<!-- MARKET_V008_SPLIT_FEASIBILITY_V0011 -->"
FILES = [
    ROOT / "docs" / "EXPERIMENTS.md",
    ROOT / "docs" / "RESEARCH_STATUS.md",
    ROOT / "docs" / "RESEARCH_DECISIONS.md",
]
BLOCK = f"""\n{MARKER}\n### Market Distributional V008 v0011 — pre-performance split-feasibility amendment\n\nThe original V008 v001 benchmark aborted before any model fit or OOS performance metric because the earliest 30% outer fold could not simultaneously satisfy 126 recent calibration origin days, 126 minimum nested validation origin days, and 500 minimum nested training origin days after purging. No V008 performance was observed.\n\nV008 v0011 preserves the frozen scientific question, features, H1/H3/H5/H10, five 30%-initial purged expanding outer folds, 126-day recent calibration window, 126-day minimum inner validation, HGB profile set, vol63_recent_calibrated primary reference, metrics, bootstrap and gates. The only scientific-control change is `minimum_inner_train_origin_days: 500 -> 378` (1.5 trading years) for nested profile selection. Final fold models remain fit on the full development block. The plan now performs a clock-only conservative split-feasibility audit before benchmarking.\n"""


def main() -> None:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = p.parse_args()
    pending = []
    for path in FILES:
        if not path.exists():
            pending.append(str(path))
            continue
        text = path.read_text(encoding="utf-8")
        if MARKER not in text:
            pending.append(str(path))
            if args.apply:
                path.write_text(text.rstrip() + "\n" + BLOCK + "\n", encoding="utf-8")
    print({"status": "PASS" if (args.apply or not pending) else "CHANGES_REQUIRED", "files_needing_patch": pending, "count": len(pending)})


if __name__ == "__main__":
    main()

from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
MARKER = "INFORMATION-SOURCE-CAPTURE-V001"
PATCHES = {
    "docs/RESEARCH_STATUS.md": """\n<!-- INFORMATION-SOURCE-CAPTURE-V001 -->\n### Information-source capture V001\nA model-invisible acquisition branch now audits and prospectively captures expectations/evidence into `information_capture_v001.db`. Initial provider research prioritizes analyst expectation/revision snapshots, official factual evidence, option-implied expectations and macro vintages. This branch is explicitly isolated from the frozen V009 prospective holdout.\n""",
    "docs/RESEARCH_DECISIONS.md": """\n<!-- INFORMATION-SOURCE-CAPTURE-V001 -->\n## Decision: begin prospective expectation capture without opening a new predictive experiment\nThe first live information acquisition is restricted to immutable strict-PIT observations. Alpha Vantage earnings calendar/estimate endpoints are a first low-operational-cost candidate; SEC remains the factual evidence backbone. Provider historical estimate backfills are not strict PIT unless vintage semantics are independently proven. V009 cannot consume these observations.\n""",
    "docs/ROADMAP.md": """\n<!-- INFORMATION-SOURCE-CAPTURE-V001 -->\n- ACTIVE parallel foundation: prospective expectation/information capture.\n- First source pilot: earnings expectations/revisions; preserve retrieval-time availability.\n- Next information audits: option-implied distribution and ALFRED macro vintages.\n- No new Market/Event model may consume these sources until a separate preregistration defines the incremental-information test.\n""",
    "docs/EXPERIMENTS.md": """\n<!-- INFORMATION-SOURCE-CAPTURE-V001 -->\n## E-INFO-CAPTURE-V001 -- prospective information acquisition (non-predictive)\nStatus: infrastructure/provider audit only. No model, target, performance metric or promotion gate. Captured observations remain model-invisible. Purpose: accumulate immutable strict-PIT expectations/evidence for a later separately preregistered experiment.\n""",
}


def main() -> None:
    p = argparse.ArgumentParser()
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--check", action="store_true")
    g.add_argument("--apply", action="store_true")
    args = p.parse_args()
    for rel, addition in PATCHES.items():
        path = ROOT / rel
        if not path.exists():
            print(f"MISSING: {path}")
            continue
        text = path.read_text(encoding="utf-8")
        if MARKER in text:
            print(f"ALREADY_PRESENT: {path}")
            continue
        if args.check:
            print(f"WOULD_APPLY: {path}")
        else:
            path.write_text(text.rstrip() + "\n" + addition.strip() + "\n", encoding="utf-8")
            print(f"APPLIED: {path}")


if __name__ == "__main__":
    main()

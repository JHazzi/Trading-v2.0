#!/usr/bin/env python3
from __future__ import annotations

import argparse
import shutil
from pathlib import Path

DATE = "20260825"

ROOT_PACKAGE_READMES = [
    "README_017_FIX.md",
    "README_BOOTSTRAP.md",
    "README_DEEP_DOCUMENTS_V001.md",
    "README_DEEP_EVENT_CORPUS_V003.md",
    "README_DEEP_EVENT_CORPUS_V003_FORM_GUARD.md",
    "README_DEEP_EVENT_CORPUS_V0031_AUDIT_FIX.md",
    "README_DEEP_EVENT_CORPUS_V0031_AUDIT_FIX2.md",
    "README_DEEP_EVENT_CORPUS_V0031_LINEAGE_FIX.md",
    "README_DEEP_HISTORY_METADATA_V001.md",
    "README_DEEP_HISTORY_METADATA_V001_FIX.md",
    "README_EVENT_BRAIN_DEEP_BENCHMARK_V0031.md",
    "README_EVENT_BRAIN_V001.md",
    "README_EVENT_BRAIN_V002.md",
    "README_EVENT_SCALE_V002.md",
    "README_FIX.md",
    "README_FIX_V05.md",
    "README_MARKET_BASELINE_V001.md",
    "README_MARKET_FOUNDATION.md",
    "README_MARKET_V002.md",
    "README_MARKET_V002_SCALE.md",
    "README_NEXT_STEP.md",
    "README_SCALE_EVENT_DATA_V001.md",
    "README_SEC_V3_LEGACY_FIX.md",
    "README_TEMPORAL_V06.md",
    "README_TEMPORAL_V07.md",
]

OLD_RESEARCH_DOCS = [
    "docs/DATA_SCALE_DECISION_V001.md",
    "docs/DEEP_DOCUMENT_SCALE_V001.md",
    "docs/DEEP_EVENT_BRAIN_BENCHMARK_V0031.md",
    "docs/DEEP_EVENT_CORPUS_V003.md",
    "docs/DEEP_EVENT_V0031_LINEAGE_DECISION.md",
    "docs/EVENT_BRAIN_DATA_SCALE_V001.md",
    "docs/EVENT_BRAIN_V001.md",
    "docs/EVENT_BRAIN_V002_DECISIONS.md",
    "docs/EVENT_MARKET_MAPPING.md",
    "docs/INTELLIGENCE_NEXT.md",
    "docs/SEC_DOCUMENT_CAUSAL_SCALE_V001.md",
]

REPLACED_DOCS = [
    "README.md",
    "ARCHITECTURE.md",
    "ARCHITECTURE_EVENT_LAYER.md",
    "AGENTS.md",
    "ingestion/events/README.md",
    "ingestion/prices/README.md",
    "features/market/README.md",
    "models/events/README.md",
    "evaluation/diagnostics/README.md",
]


def is_repo_root(root: Path) -> bool:
    return all((root / item).exists() for item in ("database", "models", "pipeline", "tests"))


def move_path(src: Path, dst: Path, apply: bool) -> None:
    if not src.exists():
        return
    print(f"MOVE  {src} -> {dst}")
    if apply:
        dst.parent.mkdir(parents=True, exist_ok=True)
        if dst.exists():
            raise RuntimeError(f"Archive destination already exists: {dst}")
        shutil.move(str(src), str(dst))


def copy_path(src: Path, dst: Path, apply: bool) -> None:
    print(f"COPY  {src} -> {dst}")
    if apply:
        if not src.exists():
            raise RuntimeError(f"Template missing: {src}")
        dst.parent.mkdir(parents=True, exist_ok=True)
        shutil.copy2(src, dst)


def main() -> None:
    ap = argparse.ArgumentParser(
        description="Safely reorganize Quant Market AI Markdown documentation."
    )
    mode = ap.add_mutually_exclusive_group(required=True)
    mode.add_argument("--plan", action="store_true")
    mode.add_argument("--apply", action="store_true")
    ap.add_argument("--root", type=Path, default=Path.cwd())
    ap.add_argument(
        "--keep-templates",
        action="store_true",
        help="Do not remove _docs_restructure_v001 after apply.",
    )
    args = ap.parse_args()

    root = args.root.resolve()
    apply = bool(args.apply)

    if not is_repo_root(root):
        raise SystemExit(f"{root} does not look like quant_market_ai repository root")

    template_root = root / "_docs_restructure_v001" / "templates"
    if not template_root.exists():
        raise SystemExit(
            "Missing _docs_restructure_v001/templates. "
            "Unzip the documentation package into the repo first."
        )

    print("MODE:", "APPLY" if apply else "PLAN")
    print("ROOT:", root)

    # Back up canonical/module docs that will be replaced.
    backup_root = root / "docs" / "archive" / "pre-restructure" / DATE
    for rel in REPLACED_DOCS:
        src = root / rel
        if src.exists():
            move_path(src, backup_root / rel, apply)

    # Archive old root package/fix readmes.
    root_archive = root / "docs" / "archive" / "package-notes" / "root"
    for rel in ROOT_PACKAGE_READMES:
        src = root / rel
        if src.exists():
            move_path(src, root_archive / rel, apply)

    # Archive old research-history docs.
    research_archive = root / "docs" / "archive" / "research-history"
    for rel in OLD_RESEARCH_DOCS:
        src = root / rel
        if src.exists():
            move_path(src, research_archive / Path(rel).name, apply)

    # Install new canonical/module docs.
    for src in sorted(template_root.rglob("*.md")):
        rel = src.relative_to(template_root)
        copy_path(src, root / rel, apply)

    if apply and not args.keep_templates:
        staging = root / "_docs_restructure_v001"
        if staging.exists():
            shutil.rmtree(staging)
            print(f"REMOVE staging {staging}")

    print()
    print("Documentation restructure", "applied." if apply else "plan complete.")
    print("Recommended review:")
    print("  git status --short")
    print("  find . -maxdepth 2 -name '*.md' | sort")
    print("  find docs/archive -type f -name '*.md' | sort | head -80")


if __name__ == "__main__":
    main()

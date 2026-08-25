# Documentation Restructure V001 — 2026-08-25

Purpose:

- replace conflicting/outdated root status READMEs with a small canonical documentation set;
- preserve all historical Markdown instead of deleting it;
- add `AGENTS.md`;
- add canonical research status, decisions, roadmap, experiment registry and data contracts;
- establish a rule for future ZIP/package Markdown.

This package intentionally changes documentation organization, not research data/model outputs.

Use:

```bash
python tools/rebuild_docs_v001.py --plan
python tools/rebuild_docs_v001.py --apply
```

Then review:

```bash
git status --short
find . -maxdepth 2 -name '*.md' | sort
```

Historical root `README_*` files are moved under `docs/archive/package-notes/root/`.
Historical files under the previous `docs/` research folder are moved under `docs/archive/research-history/`.

Existing canonical/module docs are backed up under:

```text
docs/archive/pre-restructure/20260825/
```

before replacement.

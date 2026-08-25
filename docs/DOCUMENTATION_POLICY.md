# Documentation Policy

The repository accumulated many `README_*`, `FIX`, `V001`, `V002` and package-specific Markdown files. They are valuable historical records but dangerous when mixed with current documentation.

This policy prevents that problem.

## 1. Four documentation classes

### A. Canonical

Describes current truth.

Root canonical files:

```text
README.md
ARCHITECTURE.md
ARCHITECTURE_EVENT_LAYER.md
AGENTS.md
```

Canonical current research docs:

```text
docs/INDEX.md
docs/RESEARCH_STATUS.md
docs/RESEARCH_DECISIONS.md
docs/ROADMAP.md
docs/EXPERIMENTS.md
docs/DATA_CONTRACTS.md
docs/DOCUMENTATION_POLICY.md
```

### B. Module README

Lives inside a code module and explains module responsibility, main files, current version boundary, how to test it and pointers to canonical docs.

It should not maintain an independent project roadmap.

### C. Package notes

Future ZIPs are allowed to include Markdown.

Put package/install notes under:

```text
docs/package-notes/
```

Recommended name:

```text
YYYYMMDD_<package_name>_<version>.md
```

Package notes may contain files changed, installation commands, tests, migration/recovery notes and one-time warnings.

They are not canonical project status.

### D. Archive

Superseded docs live under:

```text
docs/archive/
```

Suggested subdirectories:

```text
docs/archive/package-notes/
docs/archive/research-history/
docs/archive/pre-restructure/
```

Archive rather than delete unless a file contains secrets, generated garbage or provably duplicate content with no research value.

## 2. Root cleanliness rule

Do not add new root files matching:

```text
README_*.md
README_FIX*.md
README_NEXT_STEP.md
```

The root must stay navigable.

## 3. What to update after a result

New scientific result:

- update `RESEARCH_STATUS.md`;
- update `EXPERIMENTS.md`.

Interpretation/priority changed:

- update `RESEARCH_DECISIONS.md`;
- update `ROADMAP.md` if sequencing/gates changed.

Long-term system contract changed:

- update architecture.

A code patch that does not change research meaning does not need to rewrite architecture/status.

## 4. What a future ZIP should do

Preferred package layout:

```text
code files at their real repository paths
docs/package-notes/YYYYMMDD_package_version.md
tests/...
```

A future ZIP should **not** create another root README.

If the package materially changes current scientific state, it may also include explicit updates to canonical docs, but this must be intentional and called out.

## 5. Version truth hierarchy

If documentation conflicts:

```text
canonical current docs
    >
module README
    >
recent package note
    >
historical archive
```

Tests/database contracts still override prose when determining what code actually does.

## 6. Historical reproducibility

Do not rewrite historical package notes to make them appear current.

Archive them as they were.

New canonical docs should summarize their durable conclusions and link to machine-readable reports/code when possible.

## 7. Dates

Canonical state/roadmap docs should include a snapshot/review date.

Avoid filenames such as `FINAL_FINAL_V2.md`; use stable canonical names and let Git preserve history.

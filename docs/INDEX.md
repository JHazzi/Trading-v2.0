# Documentation Index

This is the documentation source-of-truth map.

## Canonical current documentation

| File | Purpose |
|---|---|
| `../README.md` | Project overview and context-recovery entry point |
| `../ARCHITECTURE.md` | Long-term system architecture |
| `../ARCHITECTURE_EVENT_LAYER.md` | Event/news architecture and temporal semantics |
| `../AGENTS.md` | Contributor/AI-agent rules |
| `RESEARCH_STATUS.md` | Current empirical results and limitations |
| `RESEARCH_DECISIONS.md` | Decisions, hypotheses and interpretation changes |
| `ROADMAP.md` | Ordered next work with gates |
| `EXPERIMENTS.md` | Experiment registry |
| `DATA_CONTRACTS.md` | Temporal/PIT/lineage/label contracts |
| `CONTEXT_RECOVERY.md` | Reproducible local evidence, schemas, Git/local inventory and safe cleanup |
| `TEMPORAL_DATASET_V002.md` | Explicit total shareholder return, corporate-action math and hard reconciliation gates |
| `TEMPORAL_V002_REVIEW_AND_MODEL_V001.md` | Economic entitlement review, arbitrary-tau identity and frozen horizon-conditioned preregistration |
| `TEMPORAL_DATASET_V001.md` | Horizon-conditioned raw-close outcomes, parity and selection gates |
| `DISTRIBUTIONAL_EVENT_DATASET_V002.md` | Current close-aligned dataset: verified clock provenance, quarantine and user-run audit |
| `DISTRIBUTIONAL_EVENT_DATASET_V001.md` | Rejected HTTP-modified clock contract; historical evidence only, not training data |
| `DOCUMENTATION_POLICY.md` | Documentation lifecycle and ZIP rules |

## Module documentation

Module READMEs explain implementation boundaries only. They should point back to canonical docs rather than maintain a competing roadmap.

Current module READMEs:

- `../ingestion/events/README.md`
- `../ingestion/prices/README.md`
- `../features/market/README.md`
- `../models/events/README.md`
- `../evaluation/diagnostics/README.md`

## Package notes

`package-notes/` contains installation notes for recent patches/packages.

These files are operational notes, **not project truth**. A package note may describe a one-time fix or command that is no longer appropriate after later versions.

## Historical archive

`archive/` preserves old root `README_*` files, superseded roadmaps, package/fix notes, old experiment decision docs, pre-restructure module READMEs and previous canonical docs before a major rewrite.

Do not delete the archive simply to make the repository look cleaner. The history is useful for reproducing how the research evolved.

## Updating documentation

When a scientific result changes:

1. update `RESEARCH_STATUS.md`;
2. add/update the corresponding row in `EXPERIMENTS.md`;
3. update `RESEARCH_DECISIONS.md` if interpretation/priorities change;
4. update `ROADMAP.md` only when next-work order or gates change;
5. update architecture only if a long-term contract changes.

Do not create another root `README_<version>.md`.
## Historical foundation pointer

The note below records an earlier foundation boundary, not the active roadmap.
Use RESEARCH_STATUS.md and ROADMAP.md for current gates.

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

<!-- investment-workbench-v0 -->
## Product track

- `PRODUCT_TRANSITION.md` — transition contract from research system to investment assistant.
- `INVESTMENT_WORKBENCH_V0.md` — first evidence-aware product surface and `InvestmentState` boundary.

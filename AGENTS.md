# AGENTS.md — Quant Market AI Repository Rules

This file is for human contributors and AI coding agents.

## 1. Read before changing code

Authoritative order:

1. `ARCHITECTURE.md`
2. `ARCHITECTURE_EVENT_LAYER.md`
3. `docs/RESEARCH_STATUS.md`
4. `docs/RESEARCH_DECISIONS.md`
5. `docs/ROADMAP.md`
6. `docs/DATA_CONTRACTS.md`
7. relevant module README/tests

Historical files under `docs/archive/` and package notes under `docs/package-notes/` are **not** sources of current project status.

If a historical package README conflicts with canonical docs, canonical docs win.

## 2. Research mission

The system should eventually model:

\[
P(R_{t:t+T}\mid X_t,E_t,G_t,T)
\]

Do not reduce the project to a single fixed-horizon point-return model merely because current experiments are simpler.

## 3. Non-negotiable causal rules

Never:

- use future values in features;
- use random train/test split as primary evaluation;
- treat a later correction/retrieval as historically observed without explicit non-PIT semantics;
- let event clustering reveal documents unavailable at prediction time;
- mix feature/label versions silently;
- train using version defaults when the experiment requires explicit versions;
- mark historical reconstruction strict PIT;
- use outcome columns as features.

Always:

- gate feature information using legitimate availability;
- preserve actual retrieval/observation timestamps;
- version states, labels, models and dataset contracts;
- keep prediction-time data separate from future outcomes;
- make temporal selection auditable.

## 4. Economic semantics rules

Do not hardcode predictive meaning for:

- source reliability;
- event importance;
- bullish/bearish direction;
- event lifetime/decay;
- relationship strength;
- sensationalism;
- novelty;
- graph propagation.

Deterministic taxonomy/data-quality rules are allowed. Predictive economic behavior should be learned/evaluated.

A source document is evidence. Duplicate coverage does not create independent events.

## 5. Model research rules

Every new model/component must answer:

> What incremental information does this add out of sample?

Minimum comparison set should include appropriate trivial/simple baselines.

When measuring Event Brain value, compare against a capacity-matched control so model capacity is not confused with event information.

Stochastic models require multi-seed stability before strong claims.

Use dependence-aware uncertainty estimates when labels overlap or samples share a filing/day/event.

## 6. Current checkpoint

Do not rerun or rescale data merely because a package README says so.

Current canonical status is in `docs/RESEARCH_STATUS.md`.

As of 2026-08-27:

- deep SEC corpus construction is complete for the current 10-asset research cohort;
- 1,939 normalized events and 2,001 Event States exist;
- Event H10 remains conditional/unstable and does not justify more SEC scale;
- scalar Daily Market Brain V003-V005.2 has no promoted model;
- V006 supports volatility-conditioned distribution scale;
- V007 failed against raw vol63 at all horizons;
- V008 full endogenous conditional quantiles failed significantly at all horizons;
- V008.1 passed every frozen H1 developmental gate against raw vol63 and five
  capacity placebos;
- the supported V008.1 increment is distribution shape/tails, not median
  location, direction, profitability or production readiness;
- V009 prospective confirmation is preregistered on a fixed 497-asset cohort;
- V009 requires one pre-holdout fit and the first 252 consecutive sealed H1
  origin sessions, with no refit or retrospective prediction backfill;
- no additional SEC scaling is currently justified.

## 7. Current next work

Ordered priority:

1. Run the single frozen V009 pre-holdout fit before the first eligible origin.
2. Seal every eligible daily H1 prediction within the 16-hour causal window.
3. Link outcomes separately; 126 sessions are descriptive and the first 252
   sessions are the only promotion gate.
4. Build Distributional Event Brain on existing SEC data as a separate
   developmental track against the frozen Market Brain.
5. Complete upstream identity hygiene; graph prediction remains blocked until
   direct event information adds OOS value.
6. Rich event semantics/expectations, then additional sources.
7. Graph, trajectory, risk, decision and controlled learning only through their
   explicit incremental gates.

Do not refit or tune V009 during its confirmatory window, backfill missed
predictions, or jump to mass news ingestion, graph neural networks,
Transformers or production trading without an explicit decision update.

## 8. Database/migration rules

- Prefer additive migrations.
- Never rewrite migration history already applied to real research DBs.
- Keep stable IDs deterministic when the identity contract is deterministic.
- Make reruns idempotent.
- Do not delete historical experiment rows merely because a new version supersedes them.
- Audits must fail/review when downstream stages silently produce implausibly small/empty output.
- Runtime counters are not substitutes for persisted-data audits.

## 9. Experiment version rules

A runner must explicitly state:

- `model_version`;
- `event_feature_version`;
- `market_feature_version`;
- `label_version`;
- dataset contract;
- folds/split policy;
- seed;
- bootstrap/resampling unit.

Do not rely on old V001/V002 module defaults for a V003+ experiment.

Do not overwrite historical reports/artifacts with a newer experiment.

## 10. Documentation rules

Do not create new root files named:

```text
README_FIX.md
README_<VERSION>.md
README_NEXT_STEP.md
README_PACKAGE_*.md
```

Root documentation is limited to:

- `README.md`
- `ARCHITECTURE.md`
- `ARCHITECTURE_EVENT_LAYER.md`
- `AGENTS.md`

Package/ZIP notes belong under:

```text
docs/package-notes/
```

When superseded, move them to:

```text
docs/archive/package-notes/
```

Historical decisions/old roadmap documents belong under:

```text
docs/archive/research-history/
```

If a code change changes the scientific checkpoint, also update `docs/RESEARCH_STATUS.md`, `docs/EXPERIMENTS.md`, `docs/RESEARCH_DECISIONS.md` when a decision changed, and `docs/ROADMAP.md` when priorities/gates changed.

## 11. Package/ZIP contract for future patches

A patch ZIP may contain Markdown.

It should:

- place install/package-specific notes under `docs/package-notes/`;
- not overwrite canonical docs unless the package explicitly represents a documentation/status update;
- preserve historical notes;
- include exact compile/tests/run commands;
- identify files changed and versions introduced;
- avoid instructing users to rerun expensive stages when persisted output is already valid.

## 12. Before declaring success

Check temporal causality, row counts, persisted vs reported counts, duplicate identities, feature/label versions, train/test overlap, group overlap, corporate actions, concentration, baseline performance, uncertainty interval and current research caveats.

A command completing without exception is not sufficient evidence that the scientific pipeline is correct.

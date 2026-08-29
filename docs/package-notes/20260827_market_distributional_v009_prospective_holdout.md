# Market Distributional V009 -- Prospective Holdout

## Purpose

V008.1 passed every frozen H1 developmental gate, but its hypothesis and
historical sample were informed by V008. V009 is the independent temporal
confirmation required before promoting the Market Brain distribution.

The frozen question is:

> On the first untouched prospective 252-session H1 block, does the one-time
> pre-holdout V008.1 own-state model improve equal-origin-day pinball loss over
> its frozen raw-vol63 reference?

V009 does not tune V008.1 and does not add events, graph, macro, external
proxies, costs or new model capacity.

## Frozen design

- exact V008.1 14-feature own-state family;
- exact shallow regularized HGB profile;
- H1 terminal return only;
- q05/q25/q50/q75/q95 plus positive-return probability diagnostic;
- raw vol63 reference;
- one model fit using usable targets ending strictly before 2026-08-28;
- no refit, recent calibration or feature change during confirmation;
- fixed 497-asset snapshot cohort from 2026-08-24;
- first 126 eligible origins descriptive only;
- first 252 consecutive eligible origins are the only formal gate.

The one-fit design is intentional. It prevents a new unvalidated retraining
schedule from changing the candidate during the independent holdout and is
close to the duration of the historical V008.1 outer test folds.

## Prospective clock

A prediction batch can be sealed only when:

```text
origin_day >= 2026-08-28
H1 outcome is not materialized
0 <= actual_seal_time - state_time <= 16 hours
```

The 16-hour limit places the seal before the next normal U.S. session opens.
The runner uses the actual UTC clock; it has no CLI option for a backdated seal
time.

After the first batch, every eligible Core V003 origin session must be sealed
in order. Missed eligible sessions cannot be backfilled.

## Append-only registry

Migration 021 creates the separate registry tables. Additive migration 022
hardens evaluation and audit records as immutable:

```text
preregistered experiment
frozen model fit
sealed prediction batch
candidate/reference distributions
later realized outcome
proper-score rows
evaluation runs
registry audits
```

SQL triggers reject updates/deletes to every experiment, fit, batch,
prediction, outcome, score, evaluation and audit record. Stable IDs and
payload hashes make reruns idempotent and conflicting reruns fail.

The registry is:

```text
data/processed/market_brain_v009_prospective.db
```

It is intentionally ignored by Git. The preregistration and universe manifest
remain report artifacts.

## Completed fast work

The following have already been run:

```bash
./.venv/bin/python -m pytest \
  tests/test_market_brain_distributional_v009.py \
  tests/test_market_brain_distributional_v009_settlement.py -q

./.venv/bin/python -m pipeline.market_brain_distributional_v009 --stage plan
./.venv/bin/python -m pipeline.market_brain_distributional_v009 --stage init-registry
./.venv/bin/python -m pipeline.market_brain_distributional_v009 --stage status
```

Observed fast-test result:

```text
9 passed
```

Plan result:

```text
status                       PASS
frozen universe              497 assets
feature manifest SHA-256     bd1b9868f8fb33511c953112299476e3319ec15ab4d98c127548a12b40b5005c
registry                     initialized
frozen fits                  1
sealed origin days           0
```

## Frozen fit completed

The one-time fit completed on 2026-08-27:

```text
fit_id                 fit_e5c5616664c919a2624e6daaad39d1ca
training rows          1,078,329
training last target   2026-08-24
status                 FROZEN_PRE_HOLDOUT_FIT
```

Do not rerun or replace this fit. Daily refreshes must preserve its registered
training-data and artifact hashes.

## Daily operating sequence after refresh V002 correction

The 2026-08-28 daily chart response was partial and its prediction deadline
expired without a seal. Do not build or seal that origin. Complete only the
source repair; the real five-asset check is already checkpointed:

~~~bash
./.venv/bin/python -m pipeline.market_brain_daily_refresh_v009 \
  --stage acquire --origin-day 2026-08-28
~~~

Expected result: 497 source assets and PASS_SOURCE_READY. If individual
network/provider calls fail, rerun this stage with --retry-failed.

For the first possible sealed batch, after the 2026-08-31 close and provider
settlement clock:

~~~bash
./.venv/bin/python -m pipeline.market_brain_daily_refresh_v009 \
  --stage plan --origin-day 2026-08-31

./.venv/bin/python -m pipeline.market_brain_daily_refresh_v009 \
  --stage acquire --origin-day 2026-08-31

./.venv/bin/python -m pipeline.market_brain_daily_refresh_v009 \
  --stage build-core --origin-day 2026-08-31

./.venv/bin/python -m pipeline.market_brain_daily_refresh_v009 \
  --stage readiness --origin-day 2026-08-31

./.venv/bin/python -m pipeline.market_brain_distributional_v009 \
  --stage seal --origin-day 2026-08-31
~~~

Refresh V002 never uses post-market price and never manufactures Adj Close.
Migration 023 preserves failed retrievals and exposes the first
quality-eligible observation under the documented PIT=0 reconstruction. The
Core replacement still requires its full audit and exact frozen V009 training
hash. No V009 refit occurs.
After a later Core rebuild makes the corresponding H1 labels available:

```bash
./.venv/bin/python -m pipeline.market_brain_distributional_v009 --stage settle
./.venv/bin/python -m pipeline.market_brain_distributional_v009 --stage evaluate
./.venv/bin/python -m pipeline.market_brain_distributional_v009 --stage status
```

`evaluate` is safe before 252 sessions because promotion is impossible before
the fixed confirmatory cohort is complete. At/after 126 resolved origins it
reports only the frozen first-126 descriptive cohort. At/after 252 it evaluates
only the frozen first-252 cohort; later rows cannot create a second promotion
opportunity.

## Formal confirmation gate

All checks must pass:

- block-10 lower 95% bound for raw-vol63 minus candidate pinball is positive;
- candidate mean absolute quantile calibration error is not worse;
- at least four of five chronological blocks have positive delta;
- at least three of five quantiles improve;
- continuity and prediction/outcome coverage audits pass.

A pass confirms only an H1 market-only terminal-return distribution increment.
It is not confirmed alpha, profitability, path prediction, Event Brain value,
graph value, strict-PIT historical validity or survivorship-free
generalization.

## Files introduced

```text
config/market_brain_distributional_v009.json
config/market_brain_daily_refresh_v009.json
ingestion/prices/yahoo_daily_refresh_v009.py
pipeline/market_brain_daily_refresh_v009.py
tests/test_market_brain_daily_refresh_v009.py
database/migrations/021_prospective_prediction_registry.sql
database/migrations/022_prospective_evaluation_immutability.sql
database/migrations/023_daily_price_first_quality_eligible.sql
database/apply_migration_021.py
database/apply_migration_022.py
database/apply_migration_023.py
storage/__init__.py
storage/prospective_registry.py
models/market/distributional_v009_prospective.py
evaluation/market/distributional_v009.py
pipeline/market_brain_distributional_v009.py
tests/test_market_brain_distributional_v009.py
tests/test_market_brain_distributional_v009_settlement.py
reports/market_brain_distributional_v009/prospective_holdout_v001/preregistration.json
reports/market_brain_distributional_v009/prospective_holdout_v001/universe_manifest.json
```

Canonical checkpoint updates also touch `AGENTS.md`, both architecture files,
`docs/RESEARCH_STATUS.md`, `docs/RESEARCH_DECISIONS.md`,
`docs/ROADMAP.md`, `docs/EXPERIMENTS.md` and
`docs/DATA_CONTRACTS.md`.

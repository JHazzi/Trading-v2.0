# Market Temporal Dataset V001

Status: full configured-sparse materialization complete; integrity/Core parity
PASS; long-horizon raw-close selection rejected as the primary target. V001 is
preserved as control/evidence for V002. This is a data foundation, not a model
result.

## Purpose

Build terminal raw-close outcomes parameterized by an explicit integer
exchange-session horizon:

```text
R(state, tau) = 100 * (raw_close[state + tau] / raw_close[state] - 1)
tau in {1, ..., 252}
```

The future model boundary is `Q(R | X, tau)`. Tau is a model input, not a set
of unrelated output heads and not an interpolation rule. Even a dense set of
terminal marginals is not a coherent joint future path.

## Frozen inputs and clocks

The materializer opens both inputs with SQLite `mode=ro` and `query_only`:

- `data/database/market_data_v2.db`;
- `data/processed/market_daily_v003_core.db`.

It reproduces the exact Market Core V003 source semantics:

- `daily_price_quality_gated_observations_v002`;
- latest eligible observation by observation sequence, observation time and ID;
- historical-session-close assumption, strict PIT=false;
- exact Core state origin day and raw close;
- target after exactly `tau` selected asset sessions;
- corporate-action interval `(origin_day, target_day]`;
- latest present action per asset/day/type;
- explicit `insufficient_future`, never a synthetic target.

The source cutoff is read from the Core build metadata. Later source rows cannot
silently resolve outcomes that the frozen Core still records as unavailable.
Any historical source revision that changes H1/H3/H5/H10 causes parity failure.

V009 is neither opened nor imported. No fit, prediction, outcome settlement or
prospective registry mutation is part of this stage.

## Output layout

The only dataset destination is:

```text
data/processed/market_temporal_v001.db
```

The physical layout avoids repeating long state/ticker/sector strings for every
tau:

- `temporal_price_points`: shared selected raw-close grid and observation lineage;
- `temporal_origins`: one normalized row per exact Core state;
- `temporal_corporate_actions`: latest present action lineage;
- `temporal_outcomes`: normalized `(origin_id, tau)` terminal outcomes;
- `market_temporal_v001_outcomes`: denormalized research view;
- `dataset_horizons`: tau roles and materialization flags;
- parity, selection and training-gate tables.

A candidate DB is built beside the destination and atomically replaces the
destination only after parity and integrity pass. An identical rerun reuses the
existing artifact. Changed code/config/input metadata require the explicit
`--force-rebuild` flag; the replacement is still atomic.

## Horizon strategies

The contract supports every integer tau from 1 through 252.

| Strategy | Materialized rows | Intended use |
|---|---:|---|
| `configured_sparse` | 17 taus | Default falsifiable research artifact |
| `configured_plus` | 17 checkpoints plus explicit taus | On-demand analysis without silently changing anchors |
| `dense_all` | all 252 taus | Explicit dense experiment only |

The configured 17 taus are:

```text
1 2 3 5 7 8 10 13 17 21 34 42 63 90 126 180 252
```

Training anchors remain:

```text
1 2 3 5 8 10 13 21 34 63 126 252
```

Temporal-generalization holdouts remain:

```text
7 17 42 90 180
```

Holdouts may be materialized as outcomes, but their role is persisted and they
may not enter model selection.

## Why sparse is the default

The real read-only plan observed 1,092,555 Core origins. Therefore:

```text
configured sparse: 1,092,555 * 17  =  18,573,435 outcome rows
dense all:         1,092,555 * 252 = 275,323,860 outcome rows
```

Dense is approximately 14.8 times larger before accounting for indexes and
SQLite page overhead. It does not create 14.8 times more independent evidence:
nearby taus share almost the entire price path, long targets overlap in calendar
time, and all taus for one origin share the same state.

The preferred sequence is therefore:

1. persist the shared price/action/origin grid once;
2. materialize preregistered anchors and untouched holdouts;
3. add explicit taus with `configured_plus` only for a declared analysis;
4. use `dense_all` only when a method genuinely consumes every tau and the
   storage/runtime budget is justified.

This keeps the horizon-conditioned representation general while preserving
falsifiability. Choosing favorable taus after observing their outcomes would be
horizon selection, not temporal generalization.

## Hard parity gate

H1/H3/H5/H10 are compared row-for-row with the immutable Core on:

- state/asset/origin identity;
- target trading day;
- terminal return with absolute tolerance `1e-9`;
- corporate-action overlap;
- label status.

A missing or mismatched row blocks publication of the candidate DB and writes
failure evidence to `parity_report.json`. The Core is never patched to agree.
Future training code must call `require_training_authorized`; V001 always
blocks it because a separate model protocol has not been preregistered.

## Long-horizon selection audit

H21/H63/H126/H252 are reported by:

- horizon;
- asset;
- sector;
- origin year.

For each group the report separates total, resolved, usable,
corporate-action-overlap and insufficient-future rows. The main descriptive
fraction is overlap divided by resolved outcomes. No arbitrary materiality
threshold is introduced in code.

Raw-close long-horizon training remains blocked after a successful build until
this selection is reviewed. Material overlap should trigger a separately
versioned causal total-return contract; provider Adjusted Close is not a silent
substitute.

The full review found overlap among resolved origins of 26.25%/76.20%/79.09%/
80.32% at H21/H63/H126/H252. H252 median asset overlap is 100%, with severe
sector selection. That gate triggered the separate Market Temporal V002 total
shareholder return contract. V001 remains blocked and is not rewritten.

## Exact execution

From the repository root:

```bash
python -m py_compile \
  tools/temporal_source_audit_v001.py \
  tools/temporal_dataset_v001.py

python -m unittest \
  tests.test_temporal_source_audit_v001 \
  tests.test_temporal_dataset_v001 -v

python tools/temporal_dataset_v001.py --stage plan
python tools/temporal_dataset_v001.py --stage build
python tools/temporal_dataset_v001.py --stage audit
```

Return these small reports, not the database:

```text
reports/market_temporal_v001/plan.json
reports/market_temporal_v001/parity_report.json
reports/market_temporal_v001/selection_report.json
reports/market_temporal_v001/audit.json
```

For an explicitly declared extra-tau artifact:

```bash
python tools/temporal_dataset_v001.py \
  --stage build \
  --strategy configured_plus \
  --taus 4,11,22-25 \
  --force-rebuild
```

For a dense artifact, first review the plan and storage budget:

```bash
python tools/temporal_dataset_v001.py --stage plan --strategy dense_all
```

Do not run `dense_all` by default. Do not train from the output merely because
the integrity/parity audit passes.

# Next Execution V001 — Temporal Dataset Materialization Gate

Historical execution note: this V001 run is complete. Exact parity passed and
the long-horizon selection review triggered V002. Do not rebuild V001 unless a
reviewed input/contract change requires it; continue with
`docs/NEXT_EXECUTION_V002.md`.

This work remains isolated from V009. No command in this stage trains a model,
rewrites the Market V003 Core or mutates `market_data_v2.db`.

## Completed readiness checkpoint

Local evidence established:

- canonical market source: `data/database/market_data_v2.db`;
- Market V003 Core: `data/processed/market_daily_v003_core.db`;
- 1,092,555 Core states across 497 assets and 2,260 trading sessions;
- current terminal labels exist only at H1/H3/H5/H10;
- Core target: raw close at origin to raw close `H` exchange sessions later;
- Core clock: exchange-session close;
- historical source mode remains PIT=0 reconstruction;
- source database contains daily/intraday price, session and corporate-action infrastructure.

## Immediate next command

After pulling the commit that contains the materializer:

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

Return:

```text
reports/market_temporal_v001/plan.json
reports/market_temporal_v001/parity_report.json
reports/market_temporal_v001/selection_report.json
reports/market_temporal_v001/audit.json
```

Do not return or commit the generated database.

## Temporal Dataset V001 contract

The data contract is frozen before reading any new model result.

### Time

Discrete horizons remain evaluation/materialization coordinates, not the final
model representation.

Training anchors:

```text
H1 H2 H3 H5 H8 H10 H13 H21 H34 H63 H126 H252
```

Temporal-generalization holdouts:

```text
H7 H17 H42 H90 H180
```

Those holdouts may be materialized as outcomes but may not be used for model
selection. Their role is to test whether a future `Q(R | X, tau)` model really
learns time rather than merely reproduces its training knots.

### Phase 1 — raw-close parity

Before creating any new research claim, reproduce the existing H1/H3/H5/H10
labels from the canonical source and compare them against the immutable
Market V003 Core.

Parity covers:

- target trading day;
- terminal return;
- corporate-action overlap;
- label status.

A parity failure blocks training. The old Core is evidence; it is never patched
to make the new materializer agree.

### Phase 2 — long-horizon selection audit

Only after parity, materialize/audit H21/H63/H126/H252 under the same raw-close
reference semantics.

The key question is not yet predictive performance. It is selection:

> Does excluding every raw-close window containing a corporate action destroy
> or distort the usable long-horizon cohort?

At H126/H252 this is a serious risk because recurring dividends can make
corporate-action overlap common.

Therefore raw-close long-horizon training is explicitly **not authorized**
before the overlap audit.

### Possible Phase 3 — total-return target

If the corporate-action gate shows material selection bias, define a separate
versioned total-return label contract.

That contract must reconstruct split/distribution effects from explicitly
versioned corporate-action evidence. Provider `Adjusted Close` must not be
silently substituted for a causal target.

This would be a new outcome version, not a modification of the existing
Market V003 labels.

## Intraday track

The source DB already contains:

- `price_bars`;
- `market_sessions`;
- `market_state_v002_snapshots`;
- intrasession/overnight outcome infrastructure.

The first source audit inspects schema and cheap recent samples. A deeper
interval-coverage scan comes later and remains separate from long-horizon daily
materialization.

The old ~7-session Intraday V002 result remains a research clue, not promotion
evidence.

## Full materialization

The idempotent materializer writes only to:

```text
data/processed/market_temporal_v001.db
```

The default is the 17-tau configured sparse artifact. The same contract can add
explicit taus anywhere in 1..252 with `configured_plus`, or build all 252 with
`dense_all`. Dense is supported but intentionally not the default: the real
plan estimates 275,323,860 dense outcomes versus 18,573,435 sparse outcomes.

Required gates:

1. exact H1/H3/H5/H10 parity;
2. explicit insufficient-future status;
3. corporate-action overlap diagnostics by horizon/asset/sector/year;
4. no source/Core mutation;
5. no V009 artifact access;
6. no model training in the dataset stage.

Only after those data gates close do we preregister the horizon-conditioned
distributional candidate:

```text
Q_q(R | X, tau)
```

A dense set of such marginals is still not a coherent future path. Joint path
modeling remains a later independent gate.

# Market Temporal Dataset V002

Status: full sparse materialization and mechanical review complete. Exact
parity, action reconciliation and resolved coverage pass; the downstream
special-entitlement review remains open. This is deterministic data
preparation, not a model result, path model or training authorization.

## Why V002 exists

Temporal V001 reproduced the frozen Market Core exactly, then showed that its
conservative raw-close policy does not scale to long horizons:

| Tau | Resolved windows with an action | Usable V001 rows / all origins |
|---:|---:|---:|
| 21 | 26.25% | 73.05% |
| 63 | 76.20% | 23.12% |
| 126 | 79.09% | 19.71% |
| 252 | 80.32% | 17.42% |

At H252, the median asset has an action in 100% of its resolved windows.
Excluding every dividend/split window would turn the annual target into a
non-dividend/growth-heavy selection rather than the original 497-asset cohort.

V002 therefore preserves V001 as a raw-close control and adds a separate,
versioned total-shareholder-return target.

## Mathematical contract

Let `C_s` be the provider Close on selected asset session `s`, and let `D_s`
be the sum of supported cash distributions whose effective trading day is
exactly session `s`. For one share held through the close:

```text
g_s = (C_s + D_s) / C_(s-1)
```

The terminal total return for an origin session `t` and integer asset-session
horizon `tau` is:

```text
TR(t,tau) = 100 * ( product(g_s for s=t+1..t+tau) - 1 )
```

Cash is economically received on its effective session and reinvested at that
session's close for subsequent compounding. The interval is open at the origin
and closed at the target, matching V001's corporate-action interval.

The materializer stores/log-compounds one-session factors. This is numerically
stable and gives any declared integer tau the same semantics without creating
252 separate target definitions.

## Why split factors are not multiplied

The source provider's historical `Close` and cash values are already on a
split-normalized share basis. Direct real-data checks establish this:

- AAPL before its 2020 4:1 split is stored near 125, not near 500.
- GOOGL before its 2022 20:1 split is stored near 112, not near 2,240.
- Across 138 resolved split-only steps, `Close_t / Close_(t-1)` matches the
  provider adjusted-close factor with maximum absolute factor error below
  `6.4e-7`.

Multiplying by the recorded split factor again would double-adjust the return.
V002 persists each split factor and its lineage, but its economic role is:

```text
split_lineage_provider_normalized_close
```

The factor does not enter `g_s`.

Odd split-like ratios remain visible. Some represent reorganizations or
spin-off normalization rather than textbook integer splits. Their values are
never discarded or assigned a predictive meaning.

## Adjusted Close is an audit control, not the target

Yahoo's adjusted-close convention is not identical to cash wealth return.
For a cash distribution it reconciles to:

```text
provider_control_s = C_s / (C_(s-1) - D_s)
adjusted_factor_s  = AdjC_s / AdjC_(s-1)
```

The control validates effective day, amount units and provider share basis.
It does **not** define the economic outcome.

The distinction is material for large distributions. With KDP on 2018-07-10:

```text
previous close        123.66
new close              22.19
cash distribution     103.75

economic wealth return    (22.19 + 103.75) / 123.66 - 1 = 1.84%
provider control return    22.19 / (123.66 - 103.75) - 1 = 11.45%
```

V002 uses 1.84% for the target and 11.45% only to verify that the provider's
event amount and adjusted series agree with each other.

The preimplementation source audit found that the provider control reconciles
all 15,164 selected cash-distribution steps with maximum absolute factor error
below `9.9e-7`. No-action and split-only steps also reconcile. The frozen V002
tolerance is `2e-6`; a full real build must reproduce this evidence.

## Currency and PIT interpretation

Current action rows have null explicit currency. Their units can be reconciled
to the provider-native Close/Adjusted Close basis, but cannot be described as
having verified ISO currency metadata. V002 records:

```text
provider_native_quote_unit_reconciled_not_explicit_iso_currency
```

Corporate-action observations were retrieved later than their historical
effective dates. This is acceptable for a historical outcome reconstruction,
but it is not strict PIT evidence and cannot become a prediction feature.
Actual observation/availability/version lineage is retained.

## Additive lineage

Read-only inputs:

```text
data/database/market_data_v2.db
data/processed/market_daily_v003_core.db
data/processed/market_temporal_v001.db
```

New output only:

```text
data/processed/market_temporal_v002.db
```

V001 is used as the immutable grid/parity reference. V002 compares every V001
materialized `(origin,tau)` row on target day, raw-close return, action overlap
and V001 status. It independently requires:

```text
total_return_pct == V001 return_pct
```

for every V001-usable H1/H3/H5/H10 row. Thus no-action mathematics cannot
drift while action windows receive the new economic interpretation.

## Physical layout

- `temporal_price_points`: exact V001 price grid plus audit-only Adjusted Close.
- `temporal_origins`: exact Core/V001 state origins.
- `temporal_corporate_actions`: latest-present versioned action values and lineage.
- `temporal_return_steps`: one-session economic/control factors and reconciliation.
- `temporal_outcomes`: raw-close control and total return per `(origin,tau)`.
- `coverage_by_horizon`, `coverage_by_sector`, `coverage_by_year`: recovery and quarantine.
- `training_gate`: persisted prohibition on ad-hoc model fitting.

Candidate construction is temporary and atomically published only after all
hard data gates pass. Identical reruns reuse the artifact. A changed input,
contract or code hash requires explicit `--force-rebuild`; that flag never
weakens a scientific gate.

## Outcome statuses

```text
usable                  reconstructed return passed every step gate
action_data_quarantine  at least one required action step failed reconciliation
insufficient_future     no exact target session exists inside the frozen grid
```

Action overlap is separately classified as:

```text
none
cash
split
cash_and_split
```

V001's raw-close status is also retained, so the exact number of formerly
excluded rows recovered by V002 remains auditable.

## Tau design

V002 retains the integer domain `tau=1..252`, the same 12 training anchors and
five untouched temporal-generalization holdouts as V001. Default build size:

```text
1,092,555 origins * 17 taus = 18,573,435 outcomes
```

Dense H1..H252 remains supported but is not the default. It would create
275,323,860 highly dependent rows. A later horizon-conditioned model should
use declared anchors or deterministic sampled taus and keep H7/H17/H42/H90/H180
out of selection. Dense terminal marginals are still not a coherent path.

## Training gate

Even a successful V002 build persists:

```text
BLOCKED_PENDING_V002_FULL_ACTION_REVIEW
```

The full reports now pass their mechanical gates. A separate immutable review
flags 16 cash steps above the 5% materiality threshold. Eleven can enter
model-visible outcomes and require evidence-bound entitlement decisions; five
remain lineage-only. The plan-only
`Q(total_return|X,tau)` protocol is frozen separately and remains blocked until
those decisions close. No model has been fit.

V009 is never opened, imported, refit, validated or modified.

## Exact execution

V002 execution is complete. Continue with
[NEXT_EXECUTION_TEMPORAL_REVIEW_V001.md](NEXT_EXECUTION_TEMPORAL_REVIEW_V001.md).
Do not run `dense_all`, train from the output or use `--force-rebuild` to
respond to a failed downstream gate.

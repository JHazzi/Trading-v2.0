# Data, Temporal and Leakage Contracts

This document defines the minimum causal semantics required for serious research.

## 1. Timestamp vocabulary

Different timestamps answer different questions and must not be collapsed.

### `event_time`

When the underlying economic event occurred.

### `scheduled_at`

Future known event time, if the schedule was available beforehand.

### `published_at` / acceptance time

When a source made information public.

### `observed_at` / `retrieved_at`

When our ingestion system actually observed/retrieved the information.

### `modified_at`

Its meaning is source-specific. For the SEC filing downloader this is HTTP
Last-Modified transport metadata, not a proven publication/revision clock.
Verify provenance and retain it, but do not use it to manufacture event arrival.
Actual detected byte revisions retain their separate retrieval/version clock;
unknown provenance must not be silently treated as harmless metadata.

### `available_at`

The earliest timestamp under the stated data contract at which the feature may legitimately be used by the model.

This is the primary prediction-time gate.


<!-- EVENT_T0_V001_START -->
## First-public disclosure contract

`event_time`, `first_public_at`, `published_at`, `accepted_at`,
`observed_at` and `available_at` are different concepts.

For a normalized event:

- `first_public_at` is the earliest legitimate public evidence time known
  under the evidence history;
- SEC `accepted_at` is the SEC filing acceptance timestamp and may be equal
  to, earlier than, or later than another public disclosure channel;
- `available_at` is the feature gate for a specific evidence/state row;
- later confirmation must not be back-propagated into an earlier model state.

The future multi-source Event Layer should reconstruct the evidence sequence,
not choose one source globally as the universal `t0`.

SEC is an authoritative anchor source; it is not assumed to be the fastest
source for every event.
<!-- EVENT_T0_V001_END -->

## 2. Strict PIT vs historical research reconstruction

Strict PIT requires evidence that our data representation genuinely existed by the historical cutoff.

Historical reconstruction may use a defensible historical-publication proxy, but:

- must remain marked `PIT=0`;
- must preserve later actual retrieval timestamp;
- must not pretend the bytes were observed earlier;
- must be described as research reconstruction in model reports.

Current deep SEC V003.1 corpus is historical reconstruction, not strict PIT.

## 3. Raw lineage

For SEC evidence, economic lineage is resolved through immutable raw-document/file-version/filing identity, while temporal confidence is represented separately.

A strict-PIT membership requires a temporal observation reference by the cutoff.

A non-PIT historical membership may use reconstructed availability when lineage is unambiguous.

## 4. Feature rule

For prediction state at `t`:

```text
feature source information must satisfy
legitimate available_at <= t
```

Future outcome values are forbidden in features.

A later correction may only affect historical features from the correction's legitimate availability onward.

## 5. Event clustering rule

Clustering cannot use future evidence to define a historical state.

A later-discovered duplicate/relation may produce a newer cluster observation/version, but a prediction at historical `t` cannot inherit evidence that was unavailable at `t`.

## 6. State versioning

When evidence completeness or feature semantics change materially:

- create a new feature version;
- rebuild a consistent corpus;
- do not mix old partial and new complete states under one feature version.

This rule motivated the V003.1 deep Event State rebuild.

## 7. Label rule

Labels intentionally use future market observations and therefore belong strictly to the outcome/evaluation layer.

They must never join into prediction features.

Current daily event labels include:

- terminal `return_pct`;
- `mfe_pct`;
- `mae_pct`;
- `realized_path_vol_pct`;
- corporate-action overlap status;
- resolution/availability status.

## 8. Corporate actions

Do not silently interpret raw unadjusted price jumps caused by splits/dividends as predictive market reaction.

Current conservative approach excludes overlapping corporate-action windows.

A future adjusted-return solution must itself have causal/as-of semantics.

## 9. Daily resolution

An event that occurs intraday can be ambiguous under daily bars.

Current labels mark `intraday_daily_resolution` rather than inventing false entry timing.

Do not treat those rows as clean daily event reactions without a defined policy.

The separate `distributional_event_close_aligned_v002` dataset defines such a
policy for **remaining future returns**, not immediate event reaction: the first
exchange close strictly after the validated information boundary, followed by
Core close-to-close outcomes. Intraday arrivals may enter that new contract
without relabeling the old `intraday_daily_resolution` outcomes as usable.
V001's implementation is rejected because it used HTTP modification metadata
as availability. V002 validates each clock's provenance and never moves an old
snapshot to a later date merely to satisfy the information or market-state gate.
See [DISTRIBUTIONAL_EVENT_DATASET_V002.md](DISTRIBUTIONAL_EVENT_DATASET_V002.md).

## 10. Cohort readiness

Cross-sectional features require peer context.

Scientific event-state windows must begin only after the required cohort has enough market history for the feature contract.

Current deep cohort common start:

```text
2016-09-23
```

AAPL's older price history does not justify using pre-cohort AAPL events with unavailable peer context.

## 11. Market universe semantics

`price observed` is not the same as `historical index constituent`.

Do not reinterpret an availability/universe table as true historical S&P membership unless that membership is explicitly sourced/versioned.

Current 10-company research cohort is a current-company cohort and therefore has survivorship limitations.

## 12. Train/test leakage

Primary time-series evaluation must:

- train only on earlier states;
- purge training rows whose target reaches into test;
- prevent same economic event identity crossing train/test when required;
- keep preprocessing/OOF residual fitting inside training windows.

Random split is not a valid primary estimate.

## 13. Statistical dependence is not the same as leakage

Even without future leakage, rows can be dependent:

- multiple events from one accession;
- multiple states from one event;
- same origin day;
- overlapping H10 returns;
- same sector/regime.

Use grouping, block bootstrap or sensitivity analyses rather than calling all dependence “leakage”.

## 14. Audit contract

A pipeline stage is not healthy merely because it returned exit code 0.

Audits should verify persisted outputs such as row counts, coverage, stable identities, duplicate constraints, before/after-window states, PIT flags, versions and per-asset/year/type distributions.

A completed upstream stage must not hide an empty/broken downstream stage.

## 15. Model report contract

Every training result should persist:

```text
model_version
dataset_contract
feature versions
label version
market feature version
temporal split policy
seed
resampling/bootstrap unit
rows/assets/event types
OOS date range
known PIT status
```

Do not compare experiments unless their contracts are explicit.
## 16. Prospective prediction registry contract

A prospective prediction is valid only when it is persisted before its target
outcome is available. At minimum persist:

```text
experiment/model/reference versions
frozen model-fit identity and artifact hash
origin trading day and causal state time
actual seal time
asset/state identity
exact feature snapshot and hash
distribution/probability output
```

Prediction rows are append-only and immutable. Realized outcomes and scores
are inserted later in separate tables and may not rewrite the original
prediction.

Evaluation and registry-audit rows are also append-only. Additive migration
022 enforces this at the database boundary rather than by application
convention alone.

A historical state with no currently materialized label is not automatically
prospective. The seal contract must also enforce an actual wall-clock deadline
relative to the state availability time. Missed eligible origins cannot be
backfilled and presented as live predictions.

Repeated monitoring must not create repeated promotion opportunities. A
confirmatory experiment must freeze its cohort-selection rule in advance; V009
uses the first 252 consecutive eligible sealed H1 origins. Earlier checkpoints
are descriptive unless explicitly preregistered otherwise.

The prospective registry does not convert an underlying PIT=0 historical
feature corpus into strict PIT history. Each prediction preserves the actual
state PIT flag and claims remain bounded by the universe/observation contract.


## 17. Daily regular-close fallback and first quality-eligible observation

A partial daily provider row with Open/High/Low/Volume but missing Close is not
a usable market state. Adj Close remains audit-only; missing Adj Close alone is
a warning.

Under market_brain_daily_refresh_v009_v002, a missing origin Close may be
filled only from the same provider observation's regularMarketPrice when:

- regularMarketTime belongs to the requested origin session and is no earlier
  than exchange close nor more than 300 seconds later;
- the retrieval occurs after regularMarketTime;
- the price is finite, positive and within the daily Low/High;
- exactly one provider row exists for the origin.

postMarketPrice, pre-market prices, the last intraday candle and unmarked
repair output are forbidden substitutes. Adj Close is not manufactured. Raw
lineage preserves the original daily Close, the selected source field/value and
its timestamp.

daily_price_quality_gated_observations_v002 preserves every failed retrieval.
For the explicitly PIT=0 historical reconstruction only, the first observation
that actually passes the frozen quality gate is treated as the initial
session-close assumption; actual observed_at remains unchanged. Later eligible
revisions retain retrieval-time availability. This rule must not be described
as strict PIT and cannot authorize a retrospective V009 seal.
## 18. Horizon-conditioned terminal outcome contract

Market Temporal V001 defines `tau_sessions` as an explicit positive integer in
the closed domain 1..252 eligible asset sessions. It does not encode time as a
fixed final list of model heads.

The raw-close outcome is:

```text
100 * (raw_close at asset session origin_index + tau /
       raw_close at exact Core origin_index - 1)
```

The source price selection, origin clock and action interval reproduce Market
Core V003. Corporate actions are latest-present observations per
asset/effective-day/type and overlap is open at origin, closed at target.
Provider Adjusted Close is forbidden as a silent replacement.

The output physically separates:

- shared future price/action lineage;
- exact prediction-state origins;
- future outcomes keyed by `(origin,tau)`;
- parity/selection evidence;
- a persisted training gate.

H1/H3/H5/H10 must match the immutable Core target day, return, overlap and
status row-for-row before a candidate artifact is published. Parity failure
blocks training and does not rewrite the Core.

Training anchors and temporal-generalization holdouts are metadata roles, not
different economic targets. Holdout taus may be materialized but cannot enter
model selection. Additional tau values must be declared through the configured
strategy; choosing them after observing outcomes is prohibited.

The default sparse artifact and a dense H1..H252 artifact share the same target
semantics. Dense materialization increases storage and computation but not the
effective number of independent paths. Evaluation must account for dependence
across taus, overlapping targets, origin days and assets.

This data contract creates terminal marginals only. It does not authorize a
coherent path claim, a universal interpolation formula, model training or V009
reuse.

## 19. Horizon-conditioned total shareholder return V002

Market Temporal V002 is additive to V001. It must retain V001 target day,
raw-close return, action overlap and raw-close status as control fields rather
than overwriting or reinterpreting them.

For consecutive selected provider sessions, supported cash distributions on
the effective trading day enter economic wealth as:

```text
gross_total_return_factor_s
    = (provider_close_s + cash_distribution_s)
      / provider_close_(s-1)
```

The `(origin,target]` product of those factors defines terminal total return.
Cash is reinvested at the effective-session close for subsequent compounding.
The outcome is a historical reconstruction and remains strict PIT=false;
corporate-action retrieval/availability/version lineage is preserved and may
not become an origin feature.

Current provider Close and action values are already represented on a
split-normalized share basis. A stock-split factor is persisted as outcome
lineage but is not multiplied into the return factor. Any future provider whose
Close has different semantics requires a new explicit contract/version.

Provider Adjusted Close is forbidden as a target or silent fallback. It is used
only to verify the provider convention:

```text
no cash:    provider_control = Close_s / Close_(s-1)
with cash:  provider_control = Close_s / (Close_(s-1) - cash_s)

provider_control ~= AdjClose_s / AdjClose_(s-1)
```

This control validates effective-day alignment, provider-native units and split
normalization. It does not replace the economic wealth formula; the two differ
materially for large cash distributions.

Publication requires:

- full row-for-row parity with every materialized V001 tau;
- exact H1/H3/H5/H10 identity of total return and raw return on V001 no-action
  windows;
- no missing/invalid/unreconciled selected action step;
- exact selected-session assignment for every in-grid action;
- stable read-only source/Core/V001 inputs;
- explicit `insufficient_future` and action quarantine statuses;
- a persisted training block.

Null action currency may be reported only as reconciled provider-native quote
units, never as verified ISO currency. V002 data PASS does not authorize model
training. Anchors/holdouts retain their V001 roles, dense taus remain dependent
terminal marginals and V009 remains isolated.

## 20. Temporal V002 economic-review and arbitrary-tau contract

Provider reconciliation and economic entitlement are separate gates. Every
cash step whose distribution is at least 5% of previous close requires a
decision bound to the exact V002/review hashes, evidence and rationale. A
decision never rewrites V002. An incomplete entitlement creates a versioned
downstream selection mask whose affected windows must be reported.

For V002 one-session log factors `ell_s=log(g_s)`, define the immutable prefix
`L_k=sum(ell_s,s<=k)`. Any integer tau in 1..252 is reconstructed as:

```text
100 * expm1(L_(origin_index+tau) - L_origin_index)
```

This is the same target contract as materialized checkpoints, not label
interpolation. Nonmaterialized taus used by a future experiment must be sampled
by a frozen deterministic rule. The first model preregistration deliberately
uses anchors only; sampled-tau augmentation after opening horizon holdouts is
forbidden as a rescue.

Cross-tau primary evaluation uses the origin clock on which the maximum
development tau is resolved. More recent origins remain available only in
explicit per-tau maximal-support diagnostics. Training rows are purged
individually unless their target day is strictly before the next test origin.

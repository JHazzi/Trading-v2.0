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
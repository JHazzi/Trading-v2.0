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

### `available_at`

The earliest timestamp under the stated data contract at which the feature may legitimately be used by the model.

This is the primary prediction-time gate.

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

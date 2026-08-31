# Information Integration Readiness V001

**Status:** real read-only audit PASS; no materialization and no training
**Version:** `information_integration_readiness_v001`

## Purpose

This gate answers a data question before another model is fitted:

> What information is actually present, over which assets and dates, under
> which causal clock, and what can be joined to Market Core without pretending
> that historical backfill was observed point in time?

The gate keeps five properties separate:

1. a table or document exists;
2. its economic content is known;
3. it has a legitimate `available_at` boundary;
4. it covers the Core universe/time domain and has a deterministic join;
5. it is eligible for a preregistered incremental model test.

Presence alone never implies property 2–5.

## Read-only scope

Configured inputs are:

```text
data/database/market_data_v2.db
data/processed/market_daily_v003_core.db
data/processed/market_daily_v005_external_state.db
data/processed/market_daily_v0052_financial_conditions.db
reports/distributional_event_dataset_v002/sec_core_2016_20260824_v002/dataset.sqlite
data/database/information_capture_v001.db
data/processed/event_graph_entity_registry_v002.db
data/processed/event_graph_relation_evidence_v002.db
```

Every SQLite connection uses `mode=ro` and `PRAGMA query_only=ON`. Database and
sidecar size/mtime metadata are compared before and after the audit. The V009
registry is not a configured input, is not opened and is not modified.

The tool writes only derived reports below:

```text
reports/information_integration_readiness_v001/
```

## Outputs

| Report | Question answered |
|---|---|
| `audit.json` | Did causal/read-only/schema/coverage/isolation gates pass? |
| `inventory_report.json` | Exactly which rows, dates, assets, sources, features and clocks exist? |
| `coverage_matrix.json` | How does each information layer cover the 497-asset Core domain? |
| `feature_readiness_report.json` | Which blocks are research-ready, prospective-only, blocked or absent? |
| `gap_plan.json` | What information should be acquired next, and in what order? |
| `plan.json` | How a later shared information-state artifact and incremental experiment should be designed |
| `INFORMATION_INVENTORY.md` | Human-readable local summary |

`PASS_READ_ONLY_INFORMATION_INVENTORY_CONTEXT_PLAN_READY` means only that the
inventory and next integration design are coherent. It does not promote a
feature, authorize a materializer, authorize training, validate profitability
or open any temporal holdout.

## Real inventory checkpoint — 2026-08-31

The local execution passed every hard gate. The observed holdings are:

| Layer | Observed coverage | Scientific limitation |
|---|---|---|
| Core daily state | 1,092,555 states, 497 assets, 2,260 sessions, 2017-08-25 to 2026-08-24 | historical close assumption; current-company cohort |
| Daily OHLCV source | 1,250,027 asset-days, 508 assets, 1980-12-12 to 2026-08-28 | Yahoo/yfinance; histories differ by listing age; raw version table has 499 missing closes |
| Intraday | 1,111,944 one-minute bars, 503 assets, seven trading days, 2026-08-13 to 2026-08-21 | pilot only; insufficient depth and incomplete modern availability contract |
| Legacy news | 62,671 documents, 132 sources, 503 linked assets | all bulk-ingested on 2026-08-21; no raw text, provider or document `available_at` |
| Corrected SEC event dataset | 4,086 scenarios, ten assets, 2017-08-30 to 2026-08-21 | historical reconstruction; delay scenarios are dependent |
| Prospective expectations | 5,411 metric rows, 19 assets, 19 snapshots on 2026-08-28 | all `provider_as_of` values missing; not a historical panel |
| Prospective earnings window | 1,571 tickers in one snapshot; 167 overlap Core | 60 scheduled dates were already past at capture; accumulation only |
| Graph evidence | 1,650 identity buckets and 5,929 claims for ten registrants | zero canonical buckets, zero edge-ready and zero strict-PIT claims |
| Day context | 22 SPY/QQQ/IWM and 19 VIX/rates/credit features | exact non-null coverage of all Core states; historical reconstruction |

The ten corrected SEC/event and graph registrants are AAPL, BAC, COST, CVX,
JNJ, JPM, LLY, MSFT, WMT and XOM. Prospective expectation coverage is AAPL,
AVGO, BAC, CIEN, COST, CPRT, CVX, DELL, HPE, JNJ, JPM, LLY, LULU, MDT, MSFT,
NTAP, PANW, WMT and XOM. Full 497/508-asset catalogs, per-ticker date ranges,
per-ticker news counts and all 132 news sources are stored in
`inventory_report.json`; the human summary intentionally does not copy hundreds
of ticker rows.

## Information classes

### Dense historical market context

The Core own-state, leave-one-out cross-section and sector blocks share the
exact asset/session-close origin. SPY/QQQ/IWM and the lagged VIX/rates/credit
blocks join once per `trading_day`; they are not replicated as independent
asset observations.

These layers are historical research reconstruction (`strict PIT=false`). They
may enter later incremental tests because their causal convention is explicit,
not because earlier scalar experiments promoted them. Cross-section/sector,
broad-market and financial-condition blocks remain separate hypotheses.

### Sparse historical events

The corrected SEC V002 dataset is a 10-asset historical reconstruction. Delay
scenarios are sensitivity views of the same event history, not independent
events. Event outcomes remain outcomes; they are never imported as features.

### Prospective expectations and schedules

`information_capture_v001.db` is genuinely append-only/strict-PIT, but it began
after the frozen historical Core window. Past fiscal periods inside a newly
retrieved analyst snapshot do not become historical vintages. Until repeated
snapshots and later reported facts exist, these rows are an accumulation asset,
not a backtest feature.

### Legacy news

The legacy `news_documents` corpus is inventoried rather than discarded. It
contains publication metadata, titles/summaries and asset links, but no
document-level `available_at` field and no raw text in that table. The local
ingest occurred as a bulk historical load. `published_at` therefore cannot be
silently treated as the historical model-availability clock.

A new strict-PIT news track must preserve raw bytes/hash, source, publication,
first-seen, retrieval and availability times, plus causal deduplication from
documents to economic events.

### Graph evidence

Identity buckets and structural relation claims are evidence foundations. They
are not automatically canonical entities, model-visible edges or signed
economic propagation weights. Graph prediction remains blocked until identity
hygiene is complete and direct event information adds reproducible OOS value.

## Planned shared artifact (not built by this gate)

The proposed `market_information_state_v001.db` should preserve native
statistical units:

```text
information_state_origins          one exact row per Core state_id
core_feature_block_manifest        versions/hashes/approved columns
day_context_external_market        one row per trading_day
day_context_financial_conditions   one row per trading_day
sparse_event_state_links           event/state/scenario bridge only
prospective_information_links      append-only future bridge only
integration_gates                  causal/coverage/source evidence
```

This is preferable to one denormalized table because copying market-day fields
across ~1.1 million asset states and then again across many `tau` values creates
storage and apparent sample size without new information. The model loader can
perform deterministic joins after validating block hashes and clocks.

## Incremental experiment ladder after materializer review

The strong reference remains `vol63 + tau`. New information is tested one
block at a time:

```text
Context V003-A: reference vs + cross-section/sector
Context V003-B: preregistered retained reference vs + SPY/QQQ/IWM
Context V003-C: preregistered retained reference vs + lagged VIX/rates/credit
```

Every added block needs a same-capacity deranged-block control. Development
uses only existing training anchors under purged expanding folds. H7/H17/H42/
H90/H180 remain sealed and may be opened at most once for one preregistered
contextual candidate. A failed rung cannot be rescued by stacking all blocks,
changing horizons or opening subgroups.

After the market-context ladder, the Event–Temporal Bridge compares the
market-only distribution against market plus direct event information while
preserving event/filing/content groups. Graph propagation remains later.

## Exact execution

From the repository root:

```bash
python -m py_compile tools/information_integration_readiness_v001.py
python -m unittest tests.test_information_integration_readiness_v001 -v
python tools/information_integration_readiness_v001.py
```

Review the Markdown summary first, then `audit.json`, `coverage_matrix.json`,
`feature_readiness_report.json`, `gap_plan.json` and `plan.json`.

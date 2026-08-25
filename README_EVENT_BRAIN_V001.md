# Quant Market AI — Event Brain v0.1

This package begins the Event Brain phase.

It includes:
- integration/application for the repo's existing migration 018 daily-price as-of contract;
- migration 019 for event-state snapshots, reaction labels and training runs;
- SEC factual event normalizer v0.1;
- causal event-state builder;
- daily 1/3/5/10-session reaction labels;
- first incremental residual Event Brain benchmark.

## 1. Copy package over the repo

```bash
unzip -o quant_market_ai_event_brain_v001.zip -d ~/quant_market_ai
cd ~/quant_market_ai
```

## 2. Integrate 018/019 into fresh bootstrap

```bash
python tools/integrate_018_019_bootstrap.py
python -m py_compile \
  database/apply_migration_018.py \
  database/apply_migration_019.py \
  ingestion/events/sec_event_normalizer_v001.py \
  features/events/event_state_v001.py \
  evaluation/targets/event_reaction_targets_v001.py \
  models/events/dataset_v001.py \
  models/events/train_v001.py
```

Run focused tests:

```bash
python -m pytest \
  tests/test_event_brain_v001_contract.py \
  tests/test_database_bootstrap.py \
  -q
```

## 3. Apply 018 and 019

Check first:

```bash
sqlite3 data/database/market_data_v2.db "
SELECT version, name
FROM schema_migrations
WHERE version BETWEEN '017' AND '019'
ORDER BY version;
"
```

Apply:

```bash
python database/apply_migration_018.py \
  --db data/database/market_data_v2.db

python database/apply_migration_019.py \
  --db data/database/market_data_v2.db
```

Rerun both once to verify idempotency.

## 4. Find or create a completed SEC clustering pilot

```bash
sqlite3 data/database/market_data_v2.db "
SELECT clustering_run_id, status, as_of,
       documents_considered, memberships_written, clusters_created
FROM event_clustering_runs
ORDER BY started_at DESC
LIMIT 10;
"
```

If there is no completed SEC run, do a bounded AAPL pilot. Dry-run first:

```bash
python -m ingestion.events.deterministic_clustering \
  --source sec \
  --ticker AAPL \
  --max-documents 100 \
  --run-id aapl_sec_eventbrain_pilot_v1 \
  --dry-run
```

Then persist with a NEW run id if the dry-run command records/reserves the
requested id in your current implementation, otherwise reuse only if the CLI
explicitly reports it is safe:

```bash
python -m ingestion.events.deterministic_clustering \
  --source sec \
  --ticker AAPL \
  --max-documents 100 \
  --run-id aapl_sec_eventbrain_pilot_v1_persist
```

Use the completed `clustering_run_id` below.

## 5. Normalize SEC evidence into factual events

```bash
python -m ingestion.events.sec_event_normalizer_v001 \
  --clustering-run-id <CLUSTERING_RUN_ID>
```

Copy the returned `normalization_run_id`.

Inspect:

```bash
sqlite3 data/database/market_data_v2.db "
SELECT event_type, event_subtype, COUNT(*)
FROM normalized_event_versions
GROUP BY event_type, event_subtype
ORDER BY COUNT(*) DESC;
"
```

No direction/impact/source weight should appear here.

## 6. Build causal event states

```bash
python -m features.events.event_state_v001 \
  --normalization-run-id <NORMALIZATION_RUN_ID>
```

Inspect evidence evolution:

```bash
sqlite3 data/database/market_data_v2.db "
SELECT event_type, state_time, evidence_count,
       distinct_source_count, source_signature,
       semantic_official_statement_count,
       semantic_rumor_count, semantic_opinion_count
FROM normalized_event_state_snapshots
ORDER BY state_time
LIMIT 30;
"
```

## 7. Ensure daily price history exists

```bash
sqlite3 data/database/market_data_v2.db "
SELECT a.ticker, COUNT(*) AS observations,
       MIN(o.trading_day), MAX(o.trading_day)
FROM price_bar_observations o
JOIN assets a ON a.asset_id=o.asset_id
GROUP BY a.asset_id
ORDER BY observations DESC
LIMIT 20;
"
```

If AAPL has no useful daily history, a research pilot can be ingested with the
existing Yahoo module. Example 10-year window (end is exclusive):

```bash
python -m ingestion.prices.yahoo_daily_v1 \
  --ticker AAPL \
  --exchange XNAS \
  --start 2016-08-25 \
  --end 2026-08-25 \
  --max-days 3660
```

Do not interpret this historical backfill as strict provider PIT history. The
018 contract explicitly records the session-close research assumption.

## 8. Build reaction labels

```bash
python -m evaluation.targets.event_reaction_targets_v001 \
  --horizons 1,3,5,10
```

Do NOT use `--include-intraday-coarse` for serious experiments.

Inspect:

```bash
sqlite3 data/database/market_data_v2.db "
SELECT horizon_sessions, label_status, COUNT(*)
FROM normalized_event_reaction_labels
GROUP BY horizon_sessions, label_status
ORDER BY horizon_sessions, label_status;
"
```

## 9. First predictive benchmark

This is the first actual Event Brain experiment:

```bash
python -m models.events.train_v001 \
  --horizon-sessions 1
```

Default gate is 200 model-ready rows. If it refuses because the pilot is too
small, that is correct. Do not lower the gate and interpret a five-event AAPL
experiment as evidence.

At that point scale DATA, not architecture:
- more SEC filings;
- more assets;
- daily price overlap;
- then company IR / press releases and reputable media through additional
  normalization adapters.

Once there are enough rows, run 1/3/5/10-session benchmarks.

The output compares:
- zero baseline;
- Market-only;
- Event-only;
- Market + Event residual fusion.

The key number is:

`mae_improvement_vs_market_pct`

A positive result on a proper chronological test means event context added
information beyond the market-only baseline.

## Important architectural limits of v0.1

- It does not yet model full quantile distributions/trajectories.
- It does not yet propagate through the entity graph.
- It does not infer source reliability.
- It does not use Macro yet.
- It does not automatically retrain production.

Those are subsequent layers only after incremental event signal is demonstrated.

# Event ingestion contract v0.2

No event model is trained until its evidence, temporal availability and market
reaction can be reproduced.

## Canonical layers

```text
external source
    -> raw_source_documents
    -> source-specific metadata (for example sec_filings)
    -> event clustering
    -> canonical event
    -> temporal event evidence/state
    -> realized reaction
```

Raw source documents are immutable. SQLite stores metadata, hashes and paths;
the full payload is stored compressed under `data/raw/`.

## Required temporal fields

- `published_at`: timestamp declared by the publisher, when available;
- `available_at`: earliest defensible timestamp usable by a prediction;
- `retrieved_at`: timestamp at which this system obtained the payload;
- `event_time`: time at which the underlying event occurred, if known;
- `scheduled_at`: previously known future event time, if applicable.

For SEC filings, `acceptanceDateTime` is the initial `available_at` value.
It is not inferred from the filing date.

## SEC v2 pilot

Migrations 011 and 012 must be applied before downloading filing documents:

```bash
python database/apply_migration_011.py
python database/apply_migration_012.py
```

Set an identifiable SEC user agent without committing personal contact data:

```bash
export SEC_USER_AGENT="QuantMarketAI/0.2 your-email@example.com"
```

Controlled metadata pilot:

```bash
python -m ingestion.events.sec_edgar_v2 \
  --ticker AAPL \
  --max-filings 5
```

The metadata ingestor stores filing metadata and the exact official submissions
record. The document ingestor then preserves the exact filing-index response,
populates `sec_filing_files`, and downloads the primary document plus `EX-*`
exhibits by default:

```bash
python -m ingestion.events.sec_filing_documents \
  --max-filings 5 \
  --max-files-per-filing 20 \
  --max-file-bytes 26214400 \
  --max-total-bytes 104857600
```

The archive downloader is serial and capped at 5 SEC requests per second. Raw
payloads use content-addressed gzip paths and SHA-256 over the uncompressed
response entity. An identical rerun reuses the same record. If an official URL
later returns different bytes, both observations are retained and the new
revision is not silently promoted into historical causal data.

For primary documents and exhibits, `available_at` initially inherits the
filing acceptance time. `retrieved_at` is measured after each response. A
later byte revision instead receives its actual retrieval time as availability.

## Causal rules

- A prediction at time `t` may only use evidence with
  `available_at <= t`.
- `published_at` is not blindly treated as availability for every source.
- Documents repeated by many publishers are evidence, not independent events.
- Source reliability, importance, direction and persistence are learned later;
  they are never assigned by the ingestor.
- Clustering and parsers must be deterministic and versioned.


## Deterministic document clustering foundation

Migration 015 adds an auditable clustering decision layer after migrations
010, 011, 012 and 014:

```bash
python database/apply_migration_015.py --db data/database/market_data_v2.db
```

The clustering input is deliberately limited to legacy `news_documents` and
downloaded SEC filing contents represented by `raw_source_documents`.
Documents remain evidence: this command does not create a canonical event or a
market shock, and does not assign impact, reliability, direction or decay.

Always inspect a bounded dry run first:

```bash
python -m ingestion.events.deterministic_clustering \
  --ticker AAPL \
  --start 2026-01-01T00:00:00+00:00 \
  --end 2026-08-24T23:59:59+00:00 \
  --max-documents 500 \
  --dry-run
```

Then persist the same bounded selection with an explicit run ID:

```bash
python -m ingestion.events.deterministic_clustering \
  --ticker AAPL \
  --start 2026-01-01T00:00:00+00:00 \
  --end 2026-08-24T23:59:59+00:00 \
  --max-documents 500 \
  --run-id aapl-cluster-pilot-20260824
```

The CLI defaults to `--source sec`. Using `--source news` or
`--source all` is an explicit legacy reconstruction and is recorded as not
point-in-time verified in the run metadata.

Every run first loads lightweight descriptors, normalizes all timestamps to
UTC in Python, filters the requested range, globally orders by
`available_at, evidence_type, evidence_id`, and applies `--max-documents`.
Only selected descriptors then load news bodies or gzip payloads. SEC
observation timestamps follow the same UTC normalization and causal cutoff.

A document can only match earlier evidence in that replay. Conservative paths
are the same SEC accession, normalized exact text, and a bounded
near-duplicate comparison reached through deterministic blocking. Exact text
also requires the versioned minimum token count and non-empty asset overlap;
same-accession SEC provenance remains the explicit exception. All thresholds,
time windows, fingerprint settings and candidate limits are stored in
`event_clustering_configs`; changing them requires a new
`cluster_version`.

Legacy news availability is stored as `*_assumed_not_pit_verified` with
`availability_is_point_in_time = 0`. SEC content uses filing acceptance or
revision-retrieval availability. Cluster metadata records the anchor's
availability basis and PIT flag. Retrieval observations are linked separately,
so A -> B -> A remains two immutable raw contents and three observations.

Run registration is committed before descriptor hydration and fingerprinting,
so gzip/text work does not hold a write lock. Persistence uses a second short
transaction and aborts if the database changed meanwhile. A failed run retains
`status = failed` and structured `error_json`, with no partial memberships.

`event_clusters` is only the immutable cluster/anchor identity:
`first_available_at` and `last_available_at` both remain the anchor time and
are never extended by another run. Use `event_clusters_by_run` for each
replay's first/last evidence timestamps and evidence count.

`event_cluster_memberships` plus its typed reference tables are canonical and
append-only per `clustering_run_id`. The legacy global
`event_cluster_news` table is intentionally not populated automatically,
because selections may have different scopes. Use
`event_cluster_news_by_run` for a typed, run-scoped news projection.

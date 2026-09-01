# Public Information Canonical Lake V002

## Status

Infrastructure and synthetic end-to-end tests are complete. The real plan gate
passes for build `build_b7f296888722f8cc677b8340`. Full bars/news
materialization and the final real audit are user-run. No feature or model is
authorized and V009 remains isolated.

## Purpose

V002 converts the immutable Public Information Intake V001 snapshots into
explicit economic units without claiming predictive value:

```text
minute observations -> source-specific session bars
expanded news rows   -> documents -> versions -> evidence -> asset links
                                      -> story candidates -> episode candidates
```

It does not rewrite raw Parquet, `market_data_v2.db`, Market Core or any V009
artifact. It does not average Alpaca-derived and Yahoo prices. It does not make
historical news strict PIT, turn a later article into an earlier feature, infer
market-impact `t0` as a feature or promote graph evidence to canonical identity.

## Multiple clocks

The contract rejects a universal news timestamp:

```text
event occurrence
scheduled time
first public evidence
document publication proxy
system first_seen
market-impact t0 candidate
decision availability
```

Publication and market-impact `t0` can differ in either direction. A later
document may be useful post-hoc evidence for an earlier economic episode, but
it is never model-visible before its own defensible availability. Any future
price-derived `t0` is outcome-side only.

Historical document evidence is classified as:

- `EXACT_TIME_CANDIDATE`;
- `SUSPECT_MIDNIGHT`;
- `COARSE_DAY`;
- `TIMEZONE_AMBIGUOUS`;
- `UNPARSEABLE`.

All remain `historical_strict_pit=false`. Exact candidates retain a historical
publication proxy. Coarse/day/midnight evidence receives at most a conservative
next-Core-session proxy. Every document remains usable for reconstruction and
post-hoc explanation under its recorded uncertainty.

## Document units

IDs are deterministic and versioned by policy:

```text
document_id
  canonical URL hash; exact-text fallback only when URL is missing

document_version_id
  document_id + exact text hash

story_candidate_id
  exact normalized-text hash

episode_candidate_id
  story candidate + source calendar day
```

Story and episode IDs are candidates, not semantic truth. Near-duplicate NLP
clustering remains deferred. The raw rows are not deleted. Ticker replication
becomes `news_asset_links`; collection/source repetition becomes
`news_collection_evidence`; a reused URL with changed text becomes another
document version.

## Bar units

`bar_sessions` is partitioned by year and preserves:

- premarket, RTH and after-hours counts/OHLCV;
- first-minute close, first-five-minute VWAP and first-thirty-minute VWAP;
- source row/distinct-minute counts and completeness status;
- current-symbol identity status;
- segment-end availability proxies;
- source/feed/timestamp lineage.

`bar_source_reconciliation` retains both the Alpaca-derived session values and
Yahoo daily controls. Rows are classified as near-level match, likely
adjustment regime, corporate-action review or unexplained difference. No OHLC
or volume is blended.

For historical bars, exchange/bar time is the economic clock. `first_seen` is
lineage for future/prospective capture, not a historical feature. A bar's final
OHLC is available only after the interval ends; a full RTH state is available
only after the session boundary.

## Immutable build and storage

The build id hashes configuration, frozen snapshot manifests and read-only
database/file state. Outputs live below:

```text
data/lake/public_information_v002/<build_id>/
reports/public_information_canonical_lake_v002/<build_id>/
data/database/public_information_v002_catalog.db
```

Each Parquet artifact is year-partitioned, ZSTD-compressed, SHA/tree-bound and
sealed by `_SUCCESS.json`. A matching rerun verifies and reuses it. An
unsealed existing artifact fails for review rather than being overwritten.
Stage markers bind the build and source-stability check. If Core or Market V2
changes between stages, a new build id is produced instead of silently mixing
inputs.

Managed V001 raw/lake plus V002 lake usage has a non-preallocated 100 GiB hard
cap. Real preflight observed about 20.9 GB and conservatively projects about
53.1 GB for the news stage.

## Stages

### Plan

Resolves frozen snapshots, validates contracts/storage/isolation, registers the
build and writes `plan.json`. It does not scan the full corpus.

### Bars

Scans the 531.9M-row minute file once, builds year-partitioned sessions and
source reconciliation, then writes `bars_stage.json`. On the current machine
this is expected to be the first long-running stage.

### News

Builds document versions, collection evidence, asset links, exact-normalized
story candidates and day-bounded episode candidates. It writes
`news_stage.json`. Full text stays local under the upstream rights restriction.

### Audit

Requires both stage markers and writes:

```text
bar_session_report.json
bar_reconciliation_report.json
midnight_forensics_report.json
document_identity_report.json
url_version_report.json
causal_clock_report.json
identity_coverage_report.json
storage_report.json
audit.json
```

`PASS_CANONICAL_LAKE_REVIEW_READY` means the units and lineage are reviewable.
It does not authorize training. Semantic episode resolution, historical ticker
validity, strict-PIT news and any market-impact detector remain separate gates.

## Exact execution

Do not run a Core/Market/V009 refresh concurrently. Run stages separately so a
long operation has a clear checkpoint:

```bash
cd ~/quant_market_ai

.venv/bin/python -m pipeline.public_information_canonical_lake_v002 --stage plan

.venv/bin/python -m pipeline.public_information_canonical_lake_v002 --stage bars

.venv/bin/python -m pipeline.public_information_canonical_lake_v002 --stage news

.venv/bin/python -m pipeline.public_information_canonical_lake_v002 --stage audit
```

Optional one-command execution:

```bash
.venv/bin/python -m pipeline.public_information_canonical_lake_v002 --stage all
```

Verification:

```bash
.venv/bin/python -m unittest \
  tests.test_public_information_canonical_lake_v002 \
  tests.test_public_information_semantics_audit_v001 \
  tests.test_public_information_intake_v001 -v

.venv/bin/python tools/audit_information_source_isolation_v001.py
```

Return `bars_stage.json`, `news_stage.json` and all final audit JSONs. Do not
rerun a completed stage merely because it was slow; matching artifacts are
already idempotently reusable.

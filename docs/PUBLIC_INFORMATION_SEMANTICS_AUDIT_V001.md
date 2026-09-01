# Public Information Semantics Audit V001

## Purpose and boundary

This is a read-only census of the frozen Public Information Intake V001 bars
and news snapshots. It determines what the downloaded rows mean before any
point-in-time materializer, feature or model exists. A PASS authorizes review
of a later materializer only. Training, feature visibility, canonical-source
promotion and V009 access remain blocked.

The audit reads the intake catalog, raw Parquet, `market_data_v2.db`, Market
Core V003 and the entity-registry evidence DB in read-only mode. It writes only
JSON under `reports/public_information_semantics_audit_v001/`. Input size and
mtime, including SQLite sidecars, are compared before and after execution.

## Frozen semantic distinctions

News has four separate lineage fields:

```text
collection dataset  -> fnspid_news
acquisition route   -> hf:Zihan1004/FNSPID
document domain     -> reuters.com, benzinga.com, nasdaq.com, ...
publisher/byline    -> publisher, desk, author or other preserved value
```

FNSPID is therefore not one publisher. Its paper describes 15.7 million news
records for 4,775 companies over 1999--2023 and a two-stage NASDAQ collection
route. The local multisource transformation expands document-to-ticker links,
so its row count must not be interpreted as an independent-story count.

References:

- <https://arxiv.org/abs/2402.06698>
- <https://huggingface.co/datasets/Zihan1004/FNSPID>
- <https://huggingface.co/datasets/Brianferrell787/financial-news-multisource>

Source asymmetry is measured and preserved. It is not a blocker and does not
create a reliability score. Exact/near duplicates remain evidence of document
or dissemination multiplicity, not independent economic events.

Bars preserve source-specific OHLCV. The Alpaca-derived file is a provisional
candidate with unknown feed/adjustment provenance. Yahoo is a comparison
source. The audit never overwrites, takes a median or blends volume. Official
opening auction, first minute open, overnight gap, first-minute move and
five-minute VWAP are distinct quantities.

Exact ticker equality with the current Core symbol is only a current-symbol
proxy. Historical canonical identity requires validity intervals. Existing
graph buckets are coverage evidence only and cannot auto-link a document.

## Real result: 2026-09-01

Overall status: `PASS_READ_ONLY_SEMANTICS_REVIEW_READY`. All inputs remained
unchanged, V009 interaction was `NONE`, and training/materialization remained
false.

### One-minute bars

- 531,912,667 structurally valid rows, 578 exact tickers, 2016-01-01 through
  2026-02-14; no null/nonpositive OHLC, envelope, negative-volume or negative
  trade-count violations.
- 476/497 Core tickers have an exact current-symbol match; 21 Core assets lack
  bars under that proxy and 102 bar tickers do not exactly match Core.
- Session classification contains 494,038,939 RTH, 20,823,084 premarket,
  17,049,714 after-hours and 930 outside-standard rows. No nonzero-second bars
  or duplicate minute asset-days were found.
- Core-matched RTH support is 1,174,100 asset-days. 405,857 have exactly 390
  rows; 768,243 have fewer, with median 386 and p05 234. Early closes, listing
  history, halts, missing-feed intervals and calendar semantics must therefore
  be resolved rather than treating 390 as a universal validity condition.
- 1,099,680 asset-days match Yahoo. Raw OHLC level differences have enormous
  split-like tails, while close-to-close return differences are much tighter:
  median 0.0231 percentage points, p95 0.1158, p99 0.3212. This supports a
  source/adjustment reconciliation layer, not median synthesis.
- Of 1,099,204 consecutive matched days, 28,606 have an absolute overnight gap
  at least 3%, but only 951 have a first-minute move at least 3%. A gap at the
  open is not an instantaneous after-open move. There are 453 extreme gaps on
  a recorded corporate-action effective day.

### News

- 28,741,192 rows across five collections, 22 URL domains and 1,143 normalized
  publisher/byline values; range 1999-08-31 through 2025-07-03.
- FNSPID contributes 28,606,801 expanded rows. Its leading document domains
  include Reuters (17,112,620), Benzinga (3,058,574), Nasdaq (2,491,785),
  Seeking Alpha (1,794,438), Lenta (1,601,940), Bloomberg (893,156), Zacks
  (859,116) and GuruFocus (387,328).
- Publisher is not synonymous with domain: frequent values include media names,
  desks and individual authors. 20,687,365 rows have no publisher value, while
  only 64,924 lack a URL; URL domain is therefore the deterministic source-family
  proxy for this audit.
- There are 12,816,599 distinct nonempty URL hashes among 28,676,268 rows with
  URL: 15,859,669 excess URL rows. Date+text and normalized-text diagnostics
  likewise show large replication. Deduplication must create a document/story
  identity layer while retaining ticker links and dissemination evidence.
- 17,348,022 rows have a non-midnight historical minute proxy; 11,377,647 are
  minute-labelled but exactly midnight and remain suspect/coarse; 15,523 are
  day-only next-session candidates. All historical rows remain strict PIT=false
  because first-seen/retrieval replay is absent.

### Identity and joint Core coverage

- News contains 9,220,442 source ticker links over 9,053 ticker strings. Exact
  current-symbol matching yields 2,093,826 links over 448 Core tickers; 8,605
  source tickers remain unresolved.
- `asset_identifier_history` has 508 rows but zero `valid_from` and zero
  `valid_to`. The graph has 1,650 identity evidence buckets for ten registrants
  and zero canonical buckets.
- Of 497 Core assets, 476 have bars, 448 have exact-symbol news, 433 have both
  and six have neither. This is strong breadth for exploratory integration, not
  historical identity or causal-time proof.

## Ordered continuation

1. Build **Public Information Canonical Lake V002**, not a model. Persist a
   compact, snapshot-bound daily/session bar table and retain the raw minute
   file immutable. Add exchange-calendar expectations, adjustment regime,
   corporate-action overlap and source-specific values; never average sources.
2. Build a versioned **document/story/link layer**. One document is keyed first
   by normalized URL and exact content hash; conservative near-duplicate clusters
   are separate candidates. Preserve every `(document, ticker)` and
   `(document, collection)` link so 28.6 million expanded rows never become
   28.6 million independent events.
3. Build a historical identity resolution table with explicit validity and
   confidence/evidence. Exact current symbols remain proxies until validity is
   proven. Graph evidence can nominate candidates but cannot promote them.
4. Materialize causal availability separately. Historical publication times
   remain `strict_pit=0`; day/coarse timestamps receive explicit next-session
   policy. Begin prospective `first_seen` capture for new documents.
5. Audit coverage against Core by day and asset after these transformations.
   Freeze selection masks and missingness indicators; do not silently impute
   “no news”.
6. Only then preregister one additive information test against `vol63 + tau`.
   Test blocks separately (intraday/session first, then news/event evidence),
   with equal-capacity controls and sealed temporal holdouts untouched until a
   single contextual candidate earns opening.

This sequence treats asymmetry as a property of the information environment,
while preventing source concentration, ticker replication and uncertain clocks
from masquerading as independent predictive evidence.

## Commands

```bash
.venv/bin/python tools/public_information_semantics_audit_v001.py --stage plan
.venv/bin/python tools/public_information_semantics_audit_v001.py --stage audit
.venv/bin/python -m unittest tests.test_public_information_semantics_audit_v001 -v
.venv/bin/python tools/audit_information_source_isolation_v001.py
```

The full audit scans the monolithic 531.9M-row bars Parquet and may take roughly
30 minutes on the current machine. It uses at most 6 GB of DuckDB memory and an
audit-local temporary spill directory that is removed automatically.

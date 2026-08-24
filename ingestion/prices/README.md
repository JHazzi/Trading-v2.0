# Daily Price Foundation v1

`yahoo_daily_v1.py` creates a new observational layer; it never writes to the
legacy `price_bars` table.

Contract:

- `yfinance` is called with `interval="1d"`, `auto_adjust=False` and
  `actions=True`; provider errors remain visible and a finite timeout is
  explicit. On yfinance 1.6+, error visibility uses
  `config.debug.hide_exceptions=False` and does not pass the deprecated
  `raise_errors` argument. A legacy fallback retains `raise_errors=True`.
- The canonicalized DataFrame is stored immutably under `data/raw/prices/` and
  is explicitly labeled `provider_library_output`: it is **not** claimed to be
  the exact Yahoo HTTP response. An existing content-addressed gzip is verified
  before reuse.
- A content hash identifies each unique batch. Every fetch/run also creates a
  `raw_price_batch_retrievals` observation, even when its content is identical
  to a prior batch.
- `price_bar_versions` deduplicates equal logical OHLCV rows across overlapping
  windows. `price_bar_observations` records the chronological state transition:
  `initial_backfill`, `unchanged`, `revision` or `reversion`. Therefore an
  A→B→A sequence is reconstructible without manufacturing duplicate versions.
- The primary bar identity contains unadjusted OHLCV and session boundaries.
  `adjusted_close` is audit-only: its first observed value remains on the
  version, every retrieval stores `observed_adjusted_close`, and its later
  recalculation cannot create a primary-price revision. The exact provider row
  also remains recoverable from raw.
- For the first historical OHLCV observation, `available_at` is the exchange
  session close: this is an explicit market-publication assumption, **not** a
  verified Yahoo point-in-time snapshot (`point_in_time_verified=0`).
  `observed_at`/batch `retrieved_at` preserves when this exact provider output
  entered the system. Corrections and reversions become available only at their
  later retrieval time.
- A strict system-replay backtest must cut on `observed_at`. Research that cuts
  on the inferred session-close `available_at` must disclose the lack of
  revision-free point-in-time history.
- Splits, dividends and capital gains have separate content versions and
  observations, including retractions/reversions. Retraction requires an
  explicit numeric zero in a column actually returned by the provider; a
  missing column or nonnumeric value does not manufacture absence.
- Yahoo does not expose corporate-action announcement timestamps or a reliable
  action currency in this DataFrame. Therefore
  `announcement_available_at` and action `currency` remain `NULL`, while
  causal `available_at` is retrieval time.
- A row for a session that has not closed at retrieval time remains in raw but
  is omitted from normalized EOD versions and produces a failing quality check.
- Every retrieval receives its own quality run. It compares received rows with
  all exchange sessions closed inside exclusive `[start, end)` and records
  `missing_expected_sessions`.
- Duplicate `trading_day` rows remain in immutable raw and fail quality. Every
  row for that ambiguous day is excluded from normalized bars and actions, so a
  malformed batch cannot create simultaneous revisions.
- Single-ticker yfinance MultiIndex frames are normalized; multi-ticker frames,
  naïve datetime indexes and rows outside the exclusive `[start, end)` window
  are rejected.
- Exchange-to-calendar mapping is explicit. Accepted aliases are canonicalized
  before hashing and persistence, preventing `NASDAQ` versus `XNAS` from
  creating false revisions. The calendar is built for the requested historical
  range (with boundary padding), rather than the library's short default window;
  the contract test covers AAPL's 1980-12-12 session. Pass `--exchange` if
  `assets.exchange` is empty or not recognized.
- Migration 013 validates migration identity, required columns, primary keys,
  defaults, unique identities and foreign keys inside a transaction before
  commit. Existing malformed draft tables are rejected before DDL execution.

Apply the schema after migration 011:

```bash
.venv/bin/python database/apply_migration_013.py
```

Controlled single-ticker pilot (`--end` is exclusive; default limit 366 days):

```bash
.venv/bin/python -m ingestion.prices.yahoo_daily_v1 \
  --ticker AAPL --exchange XNAS \
  --start 2025-01-01 --end 2026-01-01
```

`--raw-root` is the namespace root, normally the repository's `data/raw`.
`RawPriceStore` appends `prices/yahoo_finance/daily/<SYMBOL>/...`; passing
`data/raw/prices` would create an unintended `prices/prices/...` nesting and
is rejected by the controlled pilot. Existing batches keep their recorded
`storage_path` unchanged for auditability; do not move or delete them merely
to normalize an older path.

This source is suitable for the first reproducible historical-price pilot, not
for exchange-grade execution or an authoritative corporate-action feed.

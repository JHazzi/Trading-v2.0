# Market Distributional V009 — pre-first-seal OHLC envelope amendment

**Effective origin:** 2026-08-31

**Status:** operational source-quality amendment before the first V009 seal

**Performance observed before amendment:** no

**Sealed V009 batches before amendment:** zero

## Why this amendment exists

Yahoo's post-close daily endpoint returned internally inconsistent bars for a
small subset of the frozen 497-asset universe. The repeated responses had all
four finite positive OHLC fields, but `Open` was outside `[Low, High]`. The
existing quality gate correctly rejected those rows. Repeated retrievals showed
that Yahoo sometimes revises the fields asynchronously after the close, but a
remaining subset did not stabilize in operational time.

This is source-state information, not H1 model performance. No prospective H1
outcome existed and no V009 prediction batch had been sealed when the amendment
was defined.

## Frozen deterministic rule

For every checkpoint row whose only failed quality check is
`invalid_ohlc_rows`, after at least two repeated invalid observations:

```text
High* = max(Open, High, Low, Close)
Low*  = min(Open, High, Low, Close)
```

The rule preserves `Open`, `Close`, `Volume`, and `Adjusted Close`. It is
applied to every eligible failed row, never only to the number needed to pass
coverage. The maximum permitted expansion on either side is 2% of `Close`.
Rows beyond that cap remain quarantined.

The original provider observation and raw batch remain immutable. A separate
derived observation is appended. Migration 013 restricts the physical
`lineage_kind` column to `provider_library_output`, so the semantic
`derived_operational_repair` lineage is recorded explicitly in the immutable
raw payload/request, with `provider_library_name=quant_market_ai`, the source
observation ID, source raw batch ID, original bar hash, policy hash, and
before/after envelope values.

## Scientific boundary

The amendment does not change:

- the frozen fit or artifact;
- the target or its H1 clock;
- `Open`, `Close`, volume, adjusted close, returns, drawdowns, or volatility;
- the raw-vol63 reference;
- the 14-feature model manifest;
- the minimum 490-asset seal gate;
- the first-252 prospective cohort policy.

It can change only `asset_range_1d_pct` for repaired assets. V009 uses the
own-state feature set, so no cross-sectional feature of an unrepaired asset is
affected.

## Mandatory gates

From the effective origin onward, `repair-ohlc` must produce an immutable PASS
report. Both `build-core` and `readiness` require that report, and `seal` checks
it independently. A report is invalid if its policy changes, a repaired
observation is not quality eligible, source coverage remains below the frozen
minimum, or a repair identity cannot be reconstructed exactly.

The 2026-08-31 report is the pre-first-seal policy anchor. Later sessions may
apply the same policy hash after prior V009 batches exist, but they cannot
redefine the rule, its 2% cap, its eligibility domain, or its preserved fields.

## Execution

```bash
python -m pipeline.market_brain_daily_refresh_v009 \
  --stage repair-ohlc \
  --origin-day 2026-08-31

python -m pipeline.market_brain_daily_refresh_v009 \
  --stage build-core \
  --origin-day 2026-08-31

python -m pipeline.market_brain_daily_refresh_v009 \
  --stage readiness \
  --origin-day 2026-08-31

python -m pipeline.market_brain_distributional_v009 \
  --stage seal \
  --origin-day 2026-08-31
```

Never use manual SQL updates, delete the original observations, lower the
coverage gate, refit V009, or backfill a prediction after the frozen seal
window.

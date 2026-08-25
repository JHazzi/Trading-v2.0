# Market Brain Daily V004 — Factorized Benchmark V001

The V004 math audit passed:

```text
2260 market-day states
24860 sector-day states
1,092,555 asset-day states
497 assets / 11 sectors
dynamic beta/gamma coverage ~85.7%
identities exact to numerical precision
```

This benchmark asks whether the factorization is actually predictable.

## Primary

```text
HGB market prediction
+ HGB sector-residual prediction
+ HGB asset-residual prediction
= absolute-return prediction
```

Compare on the exact V003 OOS state rows against the stored V003 fold-specific
train-median prediction.

V003 HGB full is a secondary reference, not a weakened baseline.

## Secondary

- Ridge factorized reconstruction.
- Dynamic beta/gamma HGB reconstruction on its naturally smaller ready subset.
- Component-level diagnostics for market / sector / asset.
- Daily cross-sectional IC for reconstructed returns.

## Temporal contract

Reuse V003 V001.1 test boundaries exactly.

For market, sector and asset component training independently:

```text
target_end_day < first_test_day
```

This is necessary because each statistical level has a different row count.

## Run

```bash
python tools/patch_market_v004_factorized_benchmark_docs_v001.py --check
python tools/patch_market_v004_factorized_benchmark_docs_v001.py --apply

python -m pipeline.market_brain_daily_v004_factorized_benchmark --stage plan
```

Send the plan before running models.

If healthy:

```bash
python -m pipeline.market_brain_daily_v004_factorized_benchmark --stage run --horizon 1
python -m pipeline.market_brain_daily_v004_factorized_benchmark --stage run --horizon 3
python -m pipeline.market_brain_daily_v004_factorized_benchmark --stage run --horizon 5
python -m pipeline.market_brain_daily_v004_factorized_benchmark --stage run --horizon 10
python -m pipeline.market_brain_daily_v004_factorized_benchmark --stage summary
```

Do not add SPY/VIX/macro/events between horizons.

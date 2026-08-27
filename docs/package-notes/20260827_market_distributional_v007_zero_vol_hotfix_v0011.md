# Market Brain Distributional V007.0.1 — zero-volatility domain correction

**Status:** pre-performance implementation amendment. No V007 OOS benchmark metric was produced before this correction.

## What failed

The original V007 loader rejected any `asset_vol_20d_pct <= 0` or `asset_vol_63d_pct <= 0`. Core V003 correctly permits exact zero rolling volatility because a finite rolling standard deviation can be exactly zero. V006 already had an explicit `global_empirical_fallback` contract for nonpositive scale rows.

The previous `--stage plan` only checked files/source contracts, so it passed without inspecting scale support in the DB.

## Frozen correction

No rows are dropped and no epsilon is introduced.

- negative volatility remains a hard data error;
- observed zero volatility maps to the lower bound of the already-frozen log-ratio clip: `log(0) -> -infinity -> -max_abs_log_scale_ratio`;
- an asset whose TRAIN median volatility is nonpositive uses the positive global TRAIN median as normalizer;
- `vol20_scaled_empirical` reproduces V006's completed nonpositive-scale policy: training standardization uses positive scales and zero-scale test rows receive the global empirical distribution;
- `vol63_scaled_empirical` uses the same prospectively declared fallback;
- alpha/lambda/kappa grids, quantiles, primary reference, features, horizons, folds and scoring remain unchanged.

## Stronger plan gate

`--stage plan` now queries Core V003 and reports zero/negative/null counts for vol20 and vol63 by horizon. Exact zeros are allowed; negatives/nulls fail the plan.

## Scientific status

Because the first benchmark command aborted during data loading, before outer folds or performance metrics, this is a legitimate implementation-domain correction rather than performance-driven tuning. Preserve the old preregistration and commit this amendment before rerunning the benchmark.

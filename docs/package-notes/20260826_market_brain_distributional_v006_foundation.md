# Market Brain Distributional V006 empirical foundation

**Date:** 2026-08-26  
**Status:** benchmark completed; scientific baseline retained  
**Production status:** not production-ready

## Objective

Test whether a train-only empirical terminal-return distribution improves when its shape is standardized during training and rescaled at prediction time by causal 20-session asset volatility.

This package does not test a learned model, event information, graph information, expected-return alpha, path generation or a trading rule.

## Frozen contract

```text
benchmark_version      market_brain_distributional_v006_baseline_v001
model_version          market_brain_distributional_v006_empirical_baselines_v001
market_feature_version market_daily_state_v003_core
label_version          market_daily_reaction_v003_core
dataset_contract       market_daily_v003_all_asset_days_current_cohort_research
target                 return_pct
horizons               1,3,5,10 sessions
quantiles              0.05,0.25,0.50,0.75,0.95
folds                   5 purged expanding; initial fraction 0.30
primary unit            origin_trading_day, equal weight
bootstrap              moving blocks 5/10/20; 3,000 reps; seed 42
```

Core DB:

```text
size   3,116,351,488 bytes
sha256 2eccfe061b33bcd3fff6c244be972b379d9c4c3f1230532b5a66c72aaaf3be19
```

## Result

| Horizon | OOS rows | Primary pinball delta | 95% CI, block 10 |
|---:|---:|---:|---:|
| H1 | 763,935 | +0.009198 pp | [+0.006543, +0.011498] |
| H3 | 743,503 | +0.013490 pp | [+0.008329, +0.018274] |
| H5 | 723,573 | +0.013420 pp | [+0.007085, +0.019961] |
| H10 | 673,391 | +0.012663 pp | [+0.002568, +0.024809] |

All horizons are positive for block lengths 5/10/20. H1/H3/H5 improve in all temporal folds; H10 improves in 4/5.

Central interval coverage is close to nominal. Median MAE is essentially unchanged and positive-return Brier is slightly worse. The supported result is conditional distribution scale only.

## Files introduced

```text
config/market_brain_distributional_v006.json
evaluation/market/distributional_v006.py
evaluation/market/distributional_v006_audit.py
models/market/distributional_v006_baselines.py
pipeline/market_brain_distributional_v006.py
tests/test_market_brain_distributional_v006.py
reports/market_brain_distributional_v006/empirical_baseline_v001/
```

Canonical documents updated:

```text
README.md
ARCHITECTURE.md
docs/RESEARCH_STATUS.md
docs/RESEARCH_DECISIONS.md
docs/ROADMAP.md
docs/EXPERIMENTS.md
```

## Exact checks and commands

```bash
python -m pipeline.market_brain_distributional_v006 --stage plan
python -m pipeline.market_brain_distributional_v006 --stage audit
python -m pipeline.market_brain_distributional_v006 --stage benchmark
python -m pytest -q tests/test_market_brain_distributional_v006.py
python -m pytest -q
```

Do not rerun the benchmark merely to install or inspect this checkpoint; the persisted reports are complete. Rerun only when intentionally verifying the frozen dataset/code or creating a new explicitly versioned experiment.

## Claim boundaries

- historical price reconstruction is not strict point-in-time replay;
- the current-company research cohort is not survivorship-free;
- usable raw-close labels exclude corporate-action overlap windows;
- V006 predicts terminal-return quantiles, not a coherent path;
- no production alert, broker-cost profitability or live-trading claim follows.


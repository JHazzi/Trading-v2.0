# Market Brain Distributional V006.1 — Robustness / Falsification

## Purpose

This package does **not** create a better post-hoc V006. It freezes V006 as the completed primary and asks where its supported conditional-dispersion claim survives or fails.

V006.1 first reconstructs the exact V006 OOS predictions using the same Core V003 data, folds and empirical baseline logic. It compares the reconstructed origin-day daily-loss CSV against the persisted V006 CSV and hard-fails on mismatch. Only after reproduction passes are subgroup diagnostics accepted.

## Files introduced

```text
config/market_brain_distributional_v0061.json
evaluation/market/distributional_v0061_robustness.py
pipeline/market_brain_distributional_v0061.py
tests/test_market_brain_distributional_v0061.py
tools/patch_market_v0061_docs_v001.py
docs/package-notes/20260826_market_distributional_v0061_robustness.md
```

No existing V006 source/report is overwritten.

## Frozen diagnostics

1. Exact V006 source reproduction.
2. Tail-specific q05/q25/q50/q75/q95 pinball and calibration.
3. Direct `vol20 V006` versus `asset_empirical` reference comparison.
4. Asset contribution concentration and leave-one-asset-out sensitivity.
5. Sector contribution concentration and leave-one-sector-out sensitivity.
6. Low/mid/high volatility regimes, with 1/3 and 2/3 cut points fit on each fold's **training** `asset_vol_20d_pct` only.
7. Calibration drift in non-overlapping 126-origin-day blocks within each outer test fold.
8. Predeclared alternative causal scale sensitivities: `asset_vol_5d_pct` and `asset_vol_63d_pct`.
9. Origin-day moving-block bootstrap: frozen 5/10/20 day lengths × 3,000 reps for the global primary and direct `asset_empirical` comparison; block-10 × 1,000 reps for secondary tail/regime/alternative-scale diagnostics.

The alternative scales are diagnostics. V006.1 cannot promote one of them as a replacement primary after observing results.

## Install

From the repository root:

```bash
cd ~/quant_market_ai
unzip -o ~/Downloads/quant_market_ai_market_distributional_v0061_robustness.zip -d .
```

## Compile and tests

```bash
python -m py_compile \
  evaluation/market/distributional_v0061_robustness.py \
  pipeline/market_brain_distributional_v0061.py \
  tools/patch_market_v0061_docs_v001.py

python -m pytest \
  tests/test_market_brain_distributional_v0061.py \
  -q
```

## Preregister in canonical docs before seeing V006.1 diagnostics

```bash
python tools/patch_market_v0061_docs_v001.py --check
python tools/patch_market_v0061_docs_v001.py --apply
```

Inspect:

```bash
git diff -- \
  docs/EXPERIMENTS.md \
  docs/RESEARCH_STATUS.md \
  docs/RESEARCH_DECISIONS.md \
  docs/ROADMAP.md \
  config/market_brain_distributional_v0061.json \
  evaluation/market/distributional_v0061_robustness.py \
  pipeline/market_brain_distributional_v0061.py \
  tests/test_market_brain_distributional_v0061.py
```

## Plan gate

Run the plan before the preregistration commit so the generated `preregistration.json` is also frozen before any V006.1 diagnostic is observed:

```bash
python -m pipeline.market_brain_distributional_v0061 \
  --stage plan
```

Expected: `status = PASS`. A failure means the frozen Core V003 or completed V006 artifacts required for robustness are missing.

Now freeze code, config, canonical-doc preregistration and the plan artifact before benchmark execution:

```bash
git add -A
git commit -m "research: preregister Market Distributional V006.1 robustness"
git push
```

## Run

You may run all horizons together:

```bash
python -m pipeline.market_brain_distributional_v0061 \
  --stage benchmark \
  --horizons 1,3,5,10
```

Or one at a time without changing the scientific specification:

```bash
python -m pipeline.market_brain_distributional_v0061 --stage benchmark --horizons 1
python -m pipeline.market_brain_distributional_v0061 --stage benchmark --horizons 3
python -m pipeline.market_brain_distributional_v0061 --stage benchmark --horizons 5
python -m pipeline.market_brain_distributional_v0061 --stage benchmark --horizons 10
```

After all four exist:

```bash
python -m pipeline.market_brain_distributional_v0061 \
  --stage summary
```

## Reports created

```text
reports/market_brain_distributional_v0061/robustness_v001/
├── preregistration.json
├── h1_robustness.json
├── h1_primary_daily_losses.csv
├── h1_asset_concentration.csv
├── h1_sector_concentration.csv
├── h1_regime_summary.csv
├── h1_calibration_drift.csv
├── h1_fold_tail.csv
├── ... H3/H5/H10 ...
└── robustness_summary.json
```

## What to send back for interpretation

Send these five JSON files first:

```text
reports/market_brain_distributional_v0061/robustness_v001/h1_robustness.json
reports/market_brain_distributional_v0061/robustness_v001/h3_robustness.json
reports/market_brain_distributional_v0061/robustness_v001/h5_robustness.json
reports/market_brain_distributional_v0061/robustness_v001/h10_robustness.json
reports/market_brain_distributional_v0061/robustness_v001/robustness_summary.json
```

The CSV diagnostics remain available if a concentration/drift result needs deeper inspection.

## Interpretation boundaries

V006.1 can support statements such as:

- the V006 gain is broad or concentrated across assets/sectors;
- one or both tails drive the pinball improvement;
- the gain is regime-dependent;
- calibration drifts materially through time;
- persistent per-asset distribution shape is more competitive than dynamic vol20 at some horizons;
- vol5/vol63 sensitivity supports or challenges the specific 20-session scale choice.

It cannot establish directional alpha, expected-return skill, a coherent future trajectory, profitability after costs, strict-PIT production validity or a replacement learned model.

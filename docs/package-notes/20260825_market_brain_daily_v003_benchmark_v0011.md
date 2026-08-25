# Market Brain Daily V003 Benchmark V001.1 — pre-result hardening

No model performance was observed before these changes.

Changes relative to V001:

1. Add `asset_train_median`, the correct asset-specific constant baseline for
   an MAE/conditional-median primary objective.
2. Add always-up, always-down and train-majority direction baselines so
   directional accuracy is never compared only with a zero forecast.
3. Set HGB `early_stopping=False` explicitly. With sklearn's default `auto`,
   million-row folds would otherwise activate an internal validation split and
   choose iteration count implicitly. V001.1 instead runs the frozen 180
   boosting iterations declared in config.
4. Expose `latest_train_origin_day`, `latest_train_target_day` and
   `purged_pretest_rows` in every fold plan for visible anti-leakage auditing.
5. Add a preregistration freezer recording git HEAD, working-tree cleanliness,
   Python/numpy/pandas/sklearn versions and SHA256 for the Core DB and benchmark
   source files.

The temporal split remains `initial_fraction=0.30`. It is not changed after
seeing the calendar dates. An earlier pre-COVID start can be a later
predeclared robustness sensitivity if the primary benchmark produces a signal.

The target remains the V003 provider-Close/no-corporate-action-overlap
research target. It is not claimed to be a production total-return target.


## Install / gate

```bash
unzip -o \
  ~/Downloads/quant_market_ai_market_daily_v003_benchmark_v0011_hardening.zip \
  -d .
```

Run tests, apply the canonical-doc patch, commit/push the preregistration, then
freeze hashes:

```bash
python tools/patch_market_v003_benchmark_v0011_docs.py --check
python tools/patch_market_v003_benchmark_v0011_docs.py --apply

python -m pipeline.market_brain_daily_benchmark_v003 --stage plan
python tools/freeze_market_v003_benchmark_v0011.py
```

V001.1 reports live under:

```text
reports/market_brain_daily_v003/benchmark_v0011/
```

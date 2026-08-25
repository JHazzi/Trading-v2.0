# Market Brain Daily V004 Factorized Benchmark V001.2

V001.1 plan correctly failed before training.

The persistent coverage bug was caused by duplicated beta/gamma exposure
columns across the target and state tables. Merge suffixes created aliases
such as:

```text
beta_market_252_state
gamma_sector_252_state
```

which accidentally entered the additive feature discovery.

V001.2:

- uses `v004_asset_states` as the only exposure-feature source;
- drops target-table exposure copies before merge;
- rejects any suffixed exposure aliases;
- requires additive rows to equal all usable V004 target rows;
- requires zero missing V003 OOS states;
- freezes feature counts at 20 additive / 25 dynamic.

No factorized model performance was observed before this implementation fix.

After installation, rerun only:

```bash
python -m pipeline.market_brain_daily_v004_factorized_benchmark --stage plan
```

Do not run horizons unless V001.2 plan is PASS.

# Market Brain Daily V004 Factorized Benchmark V001.1 — coverage fix

## Why V001 was stopped before model results

The V001 plan reported:

```text
H1 additive asset_rows = 924,836
H1 dynamic_ready_rows  = 924,836
```

and the same equality at H3/H5/H10.

That is incorrect for the preregistered design. The additive reconstruction is
the primary broad-coverage candidate. Dynamic beta/gamma is a secondary
candidate whose natural coverage is ~85.7%.

Root cause: the loader built one `asset_features` list containing beta/gamma
and idiosyncratic-volatility columns, then required every one of those columns
to be finite before either candidate was separated.

Thus beta/gamma history accidentally filtered the additive primary.

No V004 factorized model performance was observed before this correction.

## V001.1 contract

Asset features are now separated:

```text
additive_asset_features
    own state
    asset-relative state
    no beta/gamma availability requirement

dynamic_asset_features
    additive features
    + beta_market_63 / 252
    + gamma_sector_63 / 252
    + idio_vol_63
```

The plan now hard-fails unless:

```text
additive coverage >= 98% of usable V004 targets
every stored V003 OOS state exists in additive coverage
dynamic beta remains a strict subset of additive coverage
```

The V001 plan remains historical. V001.1 writes to:

```text
reports/market_brain_daily_v004/factorized_benchmark_v0011/
```

## Required action

Install this overlay, rerun tests, then rerun `--stage plan`.

Do not run model horizons until V001.1 plan is PASS.

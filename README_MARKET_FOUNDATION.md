# Market Foundation v0.1

This is the first executable vertical of Quant Market AI:

`price_bars -> market state -> realized outcomes -> baseline model -> diagnostics`

## 1. Apply schema migration

```bash
python database/apply_migration.py
```

## 2. Build market state for a small sample first

```bash
python features/market/market_state_builder.py --max-assets 3 --json
```

Then inspect:

```bash
sqlite3 data/database/market_data_v2.db \
"SELECT COUNT(*) FROM market_state_snapshots;"
```

## 3. Validate realized outcomes

```bash
python evaluation/diagnostics/validate_outcomes.py
```

## 4. Generate only a controlled sample of targets

Keep using the existing `target_generator.py` first. Do not scale to the whole DB until the diagnostic report is PASS.

## 5. Train the baseline only after enough targets/state rows exist

```bash
python models/market/train.py --horizon 300
```

The first baseline is intentionally simple: RandomForestRegressor with empirical tree quantiles. It is a benchmark, not the final probabilistic architecture.

## 6. Important methodological constraints

- No random train/test split.
- Evaluation is ordered by `origin_time`.
- News, graphs and macro are intentionally excluded from v0.1.
- Do not interpret tree dispersion as calibrated market uncertainty.
- Do not deploy trading decisions from this baseline.

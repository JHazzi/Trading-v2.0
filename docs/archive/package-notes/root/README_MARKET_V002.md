# Market Baseline V002

Esta capa cierra el benchmark del modelo de mercado antes de agregar Macro/News.

## Nuevas piezas

- `global_time_split.py`: un único corte temporal global para todos los activos.
- `baselines.py`: Zero, GlobalMean y AssetMean.
- `benchmark_metrics.py`: MAE, RMSE, directional accuracy y mejora vs Zero.
- `run_market_benchmarks.py`: entrena un MarketState baseline y lo compara contra los benchmarks.
- `feature_importance.py`: ranking de features del Random Forest.

## Regla metodológica

El test no se define por número de filas. Se define por tiempo:

`train.origin_time < cutoff <= test.origin_time`

Así todos los activos respetan el mismo futuro.

## Uso

Primero correr tests:

```bash
python -m pytest -q
```

Luego benchmark:

```bash
python -m evaluation.backtest.run_market_benchmarks \
  --horizons 300,900,1800,3600
```

Y feature importance, después de entrenar el baseline:

```bash
python -m evaluation.diagnostics.feature_importance \
  --horizon 300 \
  --top 20
```

El resultado se guarda en:

`data/processed/market_benchmarks_v002.json`

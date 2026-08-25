# Market State V0.2.0

V002 agrega fuerza relativa contra mercado/sector, percentiles cross-sectional, breadth y régimen de volatilidad. V001 permanece intacto.

## Aplicar
```bash
python database/apply_migration_007.py
```

## Smoke test
```bash
python -m features.market.market_state_builder_v002 --max-assets 20 --json
```

## Escalar
```bash
python -m features.market.market_state_builder_v002 --json
```

## Entrenar
```bash
python -m models.market.train_v002 --horizon 300 --artifact models/market/artifacts/market_v002_300.pkl
python -m models.market.train_v002 --horizon 3600 --artifact models/market/artifacts/market_v002_3600.pkl
```

## Comparar
```bash
python -m evaluation.backtest.compare_market_versions --horizon 300 --v001-artifact models/market/artifacts/market_baseline_v001_300.pkl --v002-artifact models/market/artifacts/market_v002_300.pkl
python -m evaluation.backtest.compare_market_versions --horizon 3600 --v001-artifact models/market/artifacts/market_baseline_v001_3600.pkl --v002-artifact models/market/artifacts/market_v002_3600.pkl
```

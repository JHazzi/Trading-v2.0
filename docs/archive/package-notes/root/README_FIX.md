# Market Baseline v0.4 — fix

Corrige el fallo de `walk_forward` y la acumulación de outcomes.

## Cambios

- `models/market/dataset.py` es la única fuente de verdad para construir el dataset supervisado y hace el JOIN `realized_outcomes -> market_state_snapshots`.
- `train.py` y `run_walk_forward.py` usan exactamente ese mismo loader.
- `run_walk_forward.py` devuelve error claro si el dataset está vacío.
- `target_generator.py` usa identidad lógica `(asset_id, origin_time, horizon_seconds)` y `target_version`.
- `--replace` permite regenerar limpiamente un activo/horizontes durante experimentación.
- migración 005 elimina duplicados lógicos existentes y mantiene el índice único.

## Comandos

```bash
python database/apply_migration_005.py
python target_generator.py --asset-id 1 --max-origins 100 --horizons 5,15,30,60 --replace
python features/market/dataset_builder.py --horizon 300
python models/market/train.py --horizon 300
python -m evaluation.backtest.run_walk_forward --horizon 300
```

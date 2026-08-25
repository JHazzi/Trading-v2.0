# Quant Market AI — Baseline v0.5 fix

Esta versión corrige el bug de `market_state_snapshots`: las features del Market State se almacenan en `feature_snapshots` como EAV (`feature_name`, `feature_value`), no como columnas anchas. `models/market/dataset.py` ahora pivota esas features y las une a `realized_outcomes` usando `asset_id + timestamp + feature_version`.

También se mantiene compatibilidad con `python models/market/train.py` y con `python -m models.market.train`.

El `dataset_builder.py` usa ahora la misma fuente de verdad que training y walk-forward.

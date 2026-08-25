# Market features

`market_state_builder.py` convierte `price_bars` en snapshots reproducibles de estado.

`dataset_builder.py` une esos snapshots con `realized_outcomes` usando exactamente el mismo `asset_id + origin_time`, respetando la causalidad temporal.

Las columnas derivadas del futuro (`return_pct`, `mfe_pct`, `mae_pct`) son targets y nunca deben entrar al `market_state`.

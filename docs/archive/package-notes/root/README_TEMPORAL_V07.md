# Temporal v07

Corrige `--max-origins` en `target_generator.py`.

Semántica:
- `--max-origins` = máximo de origins por activo, no máximo de outcomes.
- Para intrasesión: cada origin seleccionado puede generar todos los horizontes que quepan.
- Para overnight: cada origin es un cierre de sesión y se genera como máximo un `next_open`.
- `--replace` elimina sólo el scope seleccionado y, si se da `--asset-id`, sólo ese activo.

Prueba recomendada:
```bash
python target_generator.py --asset-id 1 --scope intrasession --horizons 5,15,30,60 --max-origins 100 --replace
```

Máximo esperado:
```text
100 origins × 4 horizons = 400 outcomes
```

Luego:
```bash
sqlite3 data/database/market_data_v2.db "
SELECT horizon_value, COUNT(*)
FROM realized_outcomes
WHERE asset_id=1 AND horizon_scope='intrasession'
GROUP BY horizon_value ORDER BY horizon_value;
"
```

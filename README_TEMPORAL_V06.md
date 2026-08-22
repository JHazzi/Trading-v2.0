# Temporal Foundation v0.6

Esta versión corrige la semántica temporal del target engine.

## 1. Aplicar DB

```bash
python database/apply_migration_006.py
```

No ejecutar `sqlite3 database/migrations/006_market_sessions.sql ...`.
El `.sql` es un archivo de migración, no la DB.

## 2. Sessionizer

Instalar:

```bash
pip install exchange-calendars
```

Luego:

```bash
python -m ingestion.prices.sessionizer
```

Verificar:

```bash
sqlite3 data/database/market_data_v2.db "
SELECT session_type, COUNT(*) FROM market_sessions GROUP BY session_type;
"
```

y:

```bash
sqlite3 data/database/market_data_v2.db "
SELECT COUNT(*) FROM price_bars WHERE session_id IS NULL;
"
```

## 3. Targets intradía

Primera prueba:

```bash
python target_generator.py --asset-id 1 --scope intrasession --horizons 5,15,30,60 --max-origins 100 --replace
```

Luego todos:

```bash
python target_generator.py --scope intrasession --horizons 5,15,30,60 --replace
```

Los horizontes intradía no cruzan sesiones.

## 4. Overnight

```bash
python target_generator.py --scope overnight --replace
```

Esto crea un target `next_open` por sesión, usando:

```text
close sesión t -> open sesión t+1
```

## 5. Nota

Los outcomes viejos derivados fueron eliminados deliberadamente. Los precios, noticias, eventos y relaciones RAW permanecen intactos.

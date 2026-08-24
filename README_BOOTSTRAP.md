# Bootstrap del nuevo Quant Market AI

## 1. Preparar el entorno

Desde `~/quant_market_ai`, crear un entorno limpio e instalar las dependencias
de desarrollo fijadas por el proyecto:

```bash
python3 -m venv .venv
.venv/bin/python -m pip install -r requirements-dev.txt
```

`requirements-dev.txt` incluye `requirements.txt`, por lo que no hace falta
instalar ambos archivos por separado. Los comandos siguientes usan
explícitamente `.venv/bin/python` para no depender de otro entorno del sistema.

## 2. Crear la base vacía y llevarla al esquema canónico

Desde `~/quant_market_ai`:

```bash
.venv/bin/python database/init_db.py
```

Esto creará:

```text
data/database/market_data_v2.db
```

El script no sobrescribe una base existente. Primero aplica `schema.sql` en un
archivo temporal y después ejecuta, en orden, esta cadena canónica:

```text
001_legacy_archive.sql
002_market_foundation.sql
003_target_quality.sql
004_target_idempotency.sql
005_target_dedup.sql
006_market_sessions.sql
007_market_state_v002.sql
008_market_state_v002_390m.sql
009_asset_universe_membership.sql
010_event_layer.sql
011_source_document_foundation.sql
012_sec_filing_documents.sql
013_daily_price_observation_foundation.sql
014_sec_filing_observations.sql
015_deterministic_event_clustering.sql
```

`009_event_layer.sql` es una migración histórica obsoleta y está excluida de
la cadena. Las migraciones 013–015 son aditivas: conservan las tablas previas y
separan precios versionados, observaciones temporales SEC y clustering
determinista. La base temporal sólo reemplaza el destino una vez que existen
todas las tablas requeridas.

## 3. Auditar la base vieja

Sin modificarla:

```bash
.venv/bin/python tools/legacy_audit.py ../quant_market_bot/data/market_data.db --output data/processed/legacy_audit_report.json
```

Primero queremos revisar este reporte.

## 4. NO migrar todavía

Antes de ejecutar una migración automática hay que revisar:

- cobertura temporal de precios;
- gaps y duplicados;
- timestamps y timezone;
- cobertura por ticker;
- noticias duplicadas/relacionadas;
- campos que quedaron NULL;
- calidad de `correlaciones`;
- qué significa exactamente cada feature legacy.

## 5. Próximo paso después de la auditoría

Crear una migración `database/migrations/001_legacy_import.sql` o un script Python equivalente que:

1. importe primero datos RAW;
2. conserve IDs legacy cuando sea posible;
3. guarde trazabilidad mediante `data_lineage`;
4. valide conteos antes/después;
5. no transforme todavía targets legacy en targets definitivos.

El target `impacto_mfe_60m_pct` debe conservarse como dato histórico del sistema anterior, no como definición futura única del problema.

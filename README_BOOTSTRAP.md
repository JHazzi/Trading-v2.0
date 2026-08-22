# Bootstrap del nuevo Quant Market AI

## 1. Copiar los archivos

Copiá `ARCHITECTURE.md` a:

```bash
~/quant_market_ai/ARCHITECTURE.md
```

Copiá:

```text
 database/schema.sql
 database/init_db.py
 tools/legacy_audit.py
```

respetando la estructura del proyecto.

## 2. Crear la base vacía

Desde `~/quant_market_ai`:

```bash
python database/init_db.py
```

Esto creará:

```text
data/database/market_data_v2.db
```

El script no sobrescribe una base existente.

## 3. Auditar la base vieja

Sin modificarla:

```bash
python tools/legacy_audit.py ../quant_market_bot/data/market_data.db --output data/processed/legacy_audit_report.json
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

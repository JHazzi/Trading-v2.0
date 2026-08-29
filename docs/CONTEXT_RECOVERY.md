# Recuperación de contexto basada en evidencia

Este es el punto de entrada operativo para una IA nueva, no otro registro de
resultados científicos. La arquitectura y los documentos canónicos siguen
siendo la autoridad. Los datos se contrastan en el equipo donde realmente
existen, sin depender de que la IA anterior haya escrito un mensaje de cierre.

## Inicio rápido

Desde la raíz del proyecto, con Python 3.10 o posterior (biblioteca estándar;
no requiere instalar paquetes ni cargar el entorno de entrenamiento):

    python3 tools/project_context.py

En este entorno también sirve:

    .venv/bin/python tools/project_context.py

Desde Windows, si el proyecto sigue en esta distribución WSL:

    wsl -d Ubuntu -- bash -lc 'cd /home/trabajo/quant_market_ai && python3 tools/project_context.py'

Abrir primero [CONTEXT.md](../reports/project_context/latest/CONTEXT.md).
El informe es local y está excluido de Git. Antes de reutilizarlo:

    python3 tools/project_context.py --check

Si está vencido, cambió el proyecto o no existe, regenerarlo. Si una IA sólo
ve GitHub, debe pedir: «Ejecuta el auditor en el equipo que contiene las bases
y comparte CONTEXT.md; si necesitamos detalle, context.json». Un clon sin
bases no es evidencia de que no existan datos.

## Qué se genera

Siempre en reports/project_context/latest/, sin escribir fuera del repositorio:

| Archivo | Uso |
|---|---|
| CONTEXT.md | Resumen de entrada: hitos contrastados, bases, fuentes, experimentos y límites |
| context.json | Evidencia completa: esquema, consultas y parámetros, conteos, versiones, hashes, referencias, Git |
| REPOSITORY.md | Qué está local, ignorado, sin commit y qué se comprobó del remoto |
| CLEANUP.md | Candidatos de limpieza revisables; ninguna eliminación automática |

Se reemplazan sólo estos productos derivados. No se copian bases, raw,
predicciones ni informes experimentales completos. El JSON contiene índices,
metadatos, agregados y extractos limitados; no es un backup. Los resultados
anteriores de los experimentos permanecen en sus rutas originales.

## Cómo interpretarlo

Mantener separadas estas categorías:

- Documento: una afirmación localizada por archivo, sección o patrón.
- Medición: una consulta terminada sobre una tabla/versión concreta.
- MATCH: esa afirmación o invariante coincide con esa medición.
- MISMATCH: necesita investigación; no «arreglar» números o datos automáticamente.
- Reporte observado: existe un resultado con hash; no se volvió a ejecutar.
- UNKNOWN, TIMEOUT, MISSING o NOT_QUERIED: falta evidencia, no equivale a cero.
- REVIEW: quedan cuestiones visibles; no significa que el auditor haya fallado.
- FRESH_WITHIN_METADATA_SCOPE: los metadatos no cambiaron; no certifica ciencia.

No hay porcentaje global de progreso: contar tablas o líneas de código no mide
cuánto falta para la distribución de trayectorias definida en ARCHITECTURE.md.

Un PASS de una auditoría de datos tampoco convierte en positivo un experimento
cuyo benchmark fracasó. Se conservan ambos estados por separado. Los resultados
negativos preservan información útil y no justifican eliminar su corpus.

## Alcance de los datos

Descubre automáticamente archivos .db/.sqlite/.sqlite3 dentro del proyecto,
incluidos los que no están en data/. No sigue symlinks ni recorre .git,
entornos virtuales o node_modules. Registra las exclusiones.

Para cada base:

- ruta, tamaño, metadatos de archivo y WAL, esquema y huella del esquema;
- tablas/vistas, columnas, claves, índices, relaciones declaradas y triggers;
- conteos exactos si terminan dentro del presupuesto;
- fuentes/proveedores, versiones, banderas PIT y cobertura temporal/de activos
  en las tablas seleccionadas por el contrato (o selección automática de
  metadatos en bases nuevas/auxiliares sin selección explícita);
- historial de migraciones disponible;
- consultas acotadas para hitos y controles estructurales explícitos.

Los extremos temporales son MIN/MAX de valores almacenados; para texto son
orden lexicográfico, no una normalización universal de zonas horarias.

No se suman documentos + eventos + observaciones + estados como si fueran
unidades independientes. Tampoco se suman bases derivadas a su fuente.
Las tablas no perfiladas tienen esquema y conteo, pero no una interpretación
económica inventada. Las bases nuevas quedan como UNCLASSIFIED_DATABASE_REVIEW
hasta que exista un contrato semántico.

El registro de reportes descubre JSON nuevos automáticamente, incluso si
nadie los agregó todavía a un Markdown. Los experimentos importantes se enlazan
a secciones canónicas; una sección ausente/ambigua exige revisión.
No se ejecutan comandos encontrados en Markdown o reportes.

## Hitos congelados y bases que crecen

[El contrato](../config/project_context_v001.json) mapea afirmaciones a consultas.
Los valores esperados se leen del Markdown, no de otra copia manual de conteos.

Ejemplos de alcance explícito:

- SEC: filtrar event_state_v0031_deep y event_reaction_daily_v0031_deep;
  no mezclar el piloto o las observaciones de otras normalizaciones.
- Core: contrastar el checkpoint histórico sólo hasta 2026-08-24.
  El total actual puede aumentar legítimamente con el refresco diario.
- V009: leer el fit registrado, verificar los bytes del artefacto sin cargarlo,
  comprobar hashes de configuración/evidencia congelada y contar sellos.
- Información nueva: contar expectativas, hechos y noticias en su base aislada;
  tener filas no las convierte en features autorizadas.

El número observado de sellos V009 no prueba continuidad, elegibilidad ni
resolución válida de 252 sesiones. El auditor de contexto no ejecuta la
evaluación confirmatoria ni valida todos los controles causales. No refitea,
sella, calcula scores ni recupera predicciones retrospectivamente.

## Tiempo, frescura y concurrencia

El modo normal usa 3 segundos por consulta y 45 segundos por base. Si falta
detalle por límites de tiempo, ampliar conscientemente:

    python3 tools/project_context.py --query-seconds 12 --database-seconds 150

Para explorar únicamente estructuras:

    python3 tools/project_context.py --schema-only

Las consultas usan mode=ro, query_only y una autorización de sólo lectura.
No se usa immutable=1, porque ignoraría cambios pendientes del WAL.
No se ejecutan migraciones, VACUUM ni comprobaciones integrales de bases enormes.
La lectura puede usar la coordinación SHM normal de SQLite; no modifica tablas.

Los archivos de salida se publican con bloqueo entre escritores; el JSON se
escribe al final y --check comprueba los hashes de sus resúmenes compañeros.
Un bloqueo dejado por una interrupción requiere revisión, no borrado automático.

Los límites usan el mecanismo de progreso de SQLite; no constituyen un límite
duro frente a un sistema de archivos bloqueado. Cada consulta tiene su propia
vista de lectura, no existe una transacción global entre bases. Si cambian
data_version, el archivo/WAL o archivos del proyecto durante la auditoría,
se marca el resultado inestable y debe repetirse.

--check compara HEAD/estado Git y ruta/tamaño/mtime de archivos dentro del
alcance. Vence a las 24 horas por defecto. No vuelve a hashear decenas de GB,
no detecta modificaciones que restauren exactamente esos metadatos, ni valida
cambios del remoto. La fecha reciente no convierte un reporte histórico en
evidencia de la base actual.

## GitHub, archivos locales y privacidad

Por defecto no se usa red. Para consultar sólo la punta actual de origin/main:

    python3 tools/project_context.py --remote

No hace fetch, commit, push ni cambia el índice. El reporte distingue una
referencia remota cacheada de una consulta en vivo. Coincidir HEAD con main
no publica cambios locales sin commit.

Versionar código, SQL de esquema/migraciones, contratos/configuración sin
secretos, tests, Markdown canónico y reportes pequeños revisados.
Mantener fuera de Git bases/sidecars, raw, credenciales, entornos, artefactos
de modelos y salidas voluminosas. Lo ignorado puede ser irremplazable: necesita
respaldo y custodia separados. Un clon sólo del código no reproduce el entorno.

No lee .env, configuraciones *.local.json, claves privadas ni payloads raw.
Los extractos se minimizan y se ocultan emails/credenciales URL comunes.
Esto NO es un escáner completo de secretos ni una autorización para publicar
el informe: revisar rutas, nombres de fuentes, esquemas y cualquier metadato
sensible antes de compartirlo. La ausencia de secretos detectados no garantiza
que el repositorio sea público-seguro.

## Limpieza sin perder investigación

CLEANUP.md es sólo un plan. El JSON incluye rutas exactas, tamaño, clase, estado
Git y referencias encontradas. Compara hashes de archivos de texto pequeños;
no hashea todos los raw, modelos o bases grandes.

Nunca deducir «inútil» de una versión vieja, un resultado negativo, un nombre
similar o falta de referencias. Dos archivos idénticos pueden necesitar rutas
distintas por imports, contratos o tests. Una tabla derivada tampoco es
prescindible sin demostrar su reconstrucción con entradas/versiones disponibles.

Antes de eliminar: verificar otra vez las rutas/hashes, custodiar el original,
revisar dependencias dinámicas y tests y tomar una decisión explícita sobre
evidencia científica. Archivar notas antiguas bajo docs/archive/ cuando corresponda.
No se ofrece una opción --delete ni se genera un comando de borrado masivo.

## Extensión y pruebas

La detección de nuevos archivos, bases, tablas, versiones y reportes no requiere
un mensaje de cierre de otra IA. Sí requiere ejecutar nuevamente el auditor.
Para correlacionar un nuevo hito científico hace falta una decisión verificable:
agregar su fuente/alcance al contrato, no inventar su significado por nombre.

No se sustituyen las auditorías existentes de información, noticias, linaje,
Core o V009. Se indexan sus resultados sin ejecutarlas automáticamente.
Sus resúmenes históricos pueden sumar unidades distintas; no se adoptan esas
sumas como cantidad de eventos independientes.

    .venv/bin/python -m pytest -q tests/test_project_context.py
    .venv/bin/python -m py_compile tools/project_context.py

Códigos de salida: 0 = observación sin alertas / frescura verificada; 1 = error;
2 = informe generado con revisión pendiente; 3 = informe ausente o desactualizado.
Un 2 no debe disparar entrenamiento, reconstrucción de datos ni «reparaciones».

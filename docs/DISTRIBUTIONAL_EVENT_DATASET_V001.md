# Dataset distribucional de eventos V001

**Contrato temporal rechazado el 2026-08-28. No usar para entrenamiento ni
volver a construir V001.** La ejecución completa reveló que la fórmula de abajo
trataba metadatos HTTP Last-Modified como disponibilidad de información.
Se conserva este documento como descripción histórica, no como instrucciones
vigentes. Corrección, validación y comandos actuales:
[DISTRIBUTIONAL_EVENT_DATASET_V002.md](DISTRIBUTIONAL_EVENT_DATASET_V002.md).

El PASS de integridad del informe antiguo no certifica validez temporal.
El CLI V001 bloquea nuevos builds y señala el contrato rechazado al auditar;
no sobrescribe ni elimina los informes históricos.

Estado: infraestructura de preparación y auditoría; **no es un experimento
predictivo ni autoriza entrenamiento**. La ejecución completa y su revisión
son una puerta previa a la preregistración del Event Brain distribucional.

Este contrato implementa la primera parte de esa etapa, reutilizando los
estados SEC y Market Core existentes. No reconstruye el corpus SEC, no modifica
sus etiquetas históricas, no descarga noticias y no abre el registro ni los
artefactos de V009. La arquitectura y RESEARCH_STATUS.md siguen siendo autoridad.

## La pregunta que sí podemos hacer

¿La información de eventos disponible antes de un cierre mejora la distribución
de retornos **posteriores a ese cierre**, respecto al mismo estado de mercado?

No equivale a estimar el impacto total del anuncio, identificar su causa,
detectar la primera reacción ni demostrar que se podía operar al precio de
cierre. El resultado es una distribución terminal, no una trayectoria conjunta.

La muestra contiene días de llegada/actualización de evidencia. No representa
todos los días del mercado ni la duración económica de un evento. No hay una
regla que diga que el evento pierde efecto al día siguiente: la persistencia
queda fuera de este primer diseño. El modelo de mercado debe seguir siendo
entrenado sobre todos sus días elegibles, no sólo esta muestra de eventos.

## Relojes y disponibilidad

Nunca se igualan automáticamente:

- ocurrencia del hecho;
- publicación de cada documento;
- primera divulgación pública del hecho;
- aceptación de un filing SEC;
- captura real de los bytes;
- disponibilidad del vínculo documento–evento y de su clasificación;
- origen de la predicción;
- comienzo de una reacción estimado mirando precios posteriores.

Para evidencia `d`, la reconstrucción histórica usa una frontera conservadora:

\[
a_d^{hist}=\max(available_d, published_d, link_d, modified_d, accepted_d).
\]

Los campos opcionales ausentes no se inventan. Se exige una fecha válida con
zona horaria para cada reloj utilizado. Una fecha sin hora/zona no se transforma
silenciosamente en medianoche. `retrieved_at` se conserva por separado.

En una captura realmente operativa, la frontera debe incluir además la captura
y la disponibilidad de la representación para el predictor:

\[
a_d^{live}=\max(a_d^{hist}, observed_d, representation\_ready_d).
\]

`EvidenceClock` permite probar la parte observación/vínculo de esa álgebra.
**El adaptador de este dataset sólo admite reconstrucción histórica SEC**;
no ofrece un modo CLI que convierta estados antiguos en capturas prospectivas.
Las fechas de creación/extracción existentes se conservan como procedencia,
no se usan para simular que el código moderno existía en 2016.

La primera evidencia conocida no demuestra que sea la primera divulgación
pública. Todos los estados conservan
`UNKNOWN_EARLIER_DISCLOSURE_POSSIBLE`. Un comunicado, rumor o transmisión pudo
preceder al filing. No se interpreta esa ausencia de cobertura como ausencia
de información pública.

Una evidencia descubierta posteriormente no puede enriquecer retroactivamente
un estado anterior. Los miembros posteriores al estado se ignoran; vínculos,
anclas de clustering o clasificaciones futuras dentro de un estado se rechazan.
Los bytes identificados como `revision_observed` requieren revisión independiente
de su reloj de versión y no se admiten como el documento histórico original.
`modified_at` tampoco prueba por sí solo el contenido que existía antes.

## Alineación matemática

Para un estado de evento `s`, se valida su conjunto de evidencia `D_s` y se fija:

\[
a_s=\max(state\_time_s, available_s, observation\_available_s,
         \max_{d\in D_s}a_d^{hist}).
\]

Con `C_i` el calendario de cierres de la bolsa del activo y `delta` un retraso
adicional de sensibilidad:

\[
c_s^{\delta}=\min\{c\in C_i:c>a_s+\delta\}.
\]

La desigualdad es estricta: una evidencia fechada exactamente al cierre se
desplaza al próximo cierre. Se usa el calendario existente del repositorio,
incluyendo feriados, cierres anticipados y cambios de horario estacional.
La bolsa se comprueba en los precios observados; una identidad ausente o que
cambia de bolsa requiere revisión. No se adivina por ticker.

Se requiere el estado Core **exacto** de ese activo y cierre. Si no existe, se
registra una exclusión; no se busca una fecha posterior más conveniente.

En cada `(activo, cierre, escenario)` se conserva el último snapshot elegible
de cada evento que llega a ese cierre. Los estados anteriores del mismo evento
en esa sesión quedan como `superseded_within_session`, no se borran.

\[
X_{i,c}=MarketCore(i,c),\qquad
Y_{i,c,h}=100\left(\frac{P_{i,c+h}}{P_{i,c}}-1\right),\quad h\in\{1,3,5,10\}.
\]

`c+h` significa sesiones bursátiles, no días calendario ni simplemente la
siguiente fila disponible. Se exigen todas las sesiones del trayecto.

Ejemplo: la noticia se publica a las 14:00 y el precio ya reacciona durante la
tarde. El origen será el cierre posterior. El movimiento previo al cierre NO
forma parte de `Y`. Puede estar reflejado en `X`, que es precisamente el control
con el que se comparará la contribución adicional del evento.

Si el documento llega después del cierre, esta versión espera el siguiente
cierre. No intenta atribuir ni capturar el gap de apertura siguiente. Eso exige
otro contrato de resolución/precios, no reinterpretar estas etiquetas.

Los escenarios `delta = 0, 3600, 86400` segundos muestran sensibilidad a una
frontera más tardía. No son latencias medidas ni decaimiento económico. No se
suman como muestras independientes ni se puede elegir el mejor después de
observar rendimientos. Comparar escenarios cambia orígenes y, a veces, cohortes;
una futura evaluación debe explicitar ese cambio y sus intersecciones.

## Features y resultados separados

`samples` sólo incluye identificadores/relojes y dos proyecciones explícitas:

- Las 14 variables propias del estado Core ya fijadas por V008.1. Se reutiliza
  la especificación, **no el ajuste final de V009**.
- Conteos factuales de eventos/evidencia única, tipos y semántica; tiempo
  transcurrido desde la frontera del estado y la evidencia conocida.

Se mantienen separadas las variables candidatas de identidad/procedencia y los
resultados. No entran como features `return_pct` futuro, MFE/MAE futuros,
volatilidad futura, estado de la etiqueta, fecha de reacción inferida,
captura retrospectiva de 2026 o puntuaciones inventadas de importancia.
La codificación de tipos y cualquier procesamiento aprendido deberán fijarse
y ajustarse sólo dentro de cada entrenamiento futuro.

`outcomes` reutiliza la etiqueta Core del nuevo origen sólo después de verificar:

- activo, estado, versión, horizonte y fechas;
- cierre de origen y destino contra el calendario;
- continuidad de precios diarios y coherencia OHLC;
- ausencia de dividendos/splits/otras acciones corporativas en `(c,c+h]`;
- reproducción de retorno, MFE, MAE y volatilidad poblacional del trayecto.

En H1 la desviación poblacional de un solo retorno es cero. El universo es de
empresas actuales, con sesgo de supervivencia; los precios son reconstruidos y
la exclusión de acciones corporativas induce selección que se informa por
horizonte. Nada de esto se transforma en total return o strict PIT por pasar
una prueba de integridad.

Las etiquetas antiguas `event_reaction_daily_v0031_deep` se conservan intactas.
Sólo se copia su ID/estado/origen a `legacy_label_audit` para explicar por qué
no son intercambiables con el nuevo resultado posterior al cierre elegible.

## Duplicados y dependencia

Una sola fila por activo/cierre/escenario evita replicar el mismo retorno por
cada artículo o item del filing. La evidencia idéntica se cuenta por hash de
contenido; los vínculos y originales permanecen en la procedencia.

`sample_groups` conserva grupos de evento, accession SEC y contenido. La
utilidad `purged_partition` elimina del entrenamiento tanto resultados que
alcancen el inicio del test como grupos compartidos con el test. Debe aplicarse
también en particiones internas. No selecciona los folds del futuro benchmark.

Esto no resuelve universalmente eventos económicos repetidos entre filings
distintos. Los vínculos cross-accession ambiguos se excluyen para revisión.
Todavía se necesitan incertidumbre por bloques de días, concentración por
activo/tipo/filing y controles de capacidad equivalentes.

Antes de entrenar habrá que preregistrar horizonte primario, folds, familias,
semillas, pesos, controles y criterio incremental. Para cada test histórico,
el modelo de mercado se ajusta únicamente con targets anteriores a ese test.
El ajuste V009 entrenado hasta agosto de 2026 no sirve como predicción OOS de
eventos de 2020.

## Ejecución

Desde la raíz del repositorio, en WSL y usando el entorno del proyecto:

```bash
.venv/bin/python -m research.events.distributional_dataset_v001 --stage build --run-id sec_core_2016_20260824_v001
```

Ésta es la ejecución completa que queda a cargo del usuario. No entrena ni
descarga, pero consulta el corpus y verifica etiquetas. Evitar ejecutarla
simultáneamente con escrituras/refrescos de las dos bases de entrada. Si hay
cambios concurrentes se marca `INPUTS_CHANGED_DURING_BUILD`; no se considera
una fotografía consistente.

Si una consulta excede el límite de 30 segundos, se aborta. Después de revisar
el motivo puede usarse `--query-seconds 120` y un nuevo `--run-id`. Ese límite
es por consulta, no por ejecución completa ni por acceso bloqueado al disco.

Para una prueba pequeña, seleccionada uniformemente en el orden temporal sin
mirar etiquetas:

```bash
.venv/bin/python -m research.events.distributional_dataset_v001 --stage build --run-id smoke_local_v001 --max-states 12
```

`--max-states` SIEMPRE marca ejecución parcial. No debe interpretarse como una
auditoría de cobertura, y en una muestra puede haber menos activos/horizontes.

Los resultados completos quedan en:

```text
reports/distributional_event_dataset_v001/sec_core_2016_20260824_v001/
  AUDIT.md        resumen para usuario/IA
  audit.json      conteos, cobertura y motivos de exclusión
  manifest.json   versiones, huellas, entorno y alcance
  dataset.sqlite features, resultados separados y procedencia por fila
```

Para verificar nuevamente ese resultado, sin reconstruirlo ni abrir las fuentes:

```bash
.venv/bin/python -m research.events.distributional_dataset_v001 --stage audit --run-id sec_core_2016_20260824_v001
```

Un build repetido con entradas/configuración/código idénticos reutiliza y audita
el resultado sin reescribirlo. Si cambian, exige otro nombre. Una ejecución
interrumpida tampoco se sobrescribe automáticamente.

Códigos de salida: `1` = fallo; `2` = informe generado con revisión científica
pendiente. `integrity_status=PASS` sólo verifica este contrato de preparación;
el estado global permanece `REVIEW`, `training_authorized=false`.
Una excepción puede producir únicamente una base incompleta: sin manifiesto
y auditoría terminados NO hay un dataset listo para revisión.

El manifiesto registra metadatos main/WAL de las fuentes y huellas del contenido
seleccionado/código. No hashea todos los GB de las fuentes, ni certifica todo el
pipeline histórico. La auditoría reabre la salida y reproduce sus controles;
el modo CLI comprueba además el hash del archivo de salida.

Compartir `AUDIT.md` y `audit.json` para decidir el siguiente paso. No publicar
la base/raw en Git. Código, tests y contrato sí son versionables; la salida
local está ignorada y es reconstruible conservando sus fuentes/versiones.

## Pruebas rápidas

```bash
.venv/bin/python -m py_compile research/events/distributional_dataset_v001.py
.venv/bin/python -m pytest -q tests/test_distributional_event_dataset_v001.py
```

Incluyen horas sin zona, retraso de captura, evidencia/confirmaciones futuras,
anclas de clustering futuras, revisiones de bytes, duplicados, calendario,
etiquetas cruzadas, precios faltantes, acciones corporativas, contaminación de
features, corrupción persistida, repetición segura y purga temporal/grupal.

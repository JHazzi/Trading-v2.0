# Dataset distribucional de eventos V002

Estado: corrección de preparación de datos; **no autoriza entrenamiento**.
Sustituye el contrato temporal V001 sin sobrescribir sus bases ni informes.
La arquitectura y los documentos canónicos siguen siendo la autoridad.

## Qué se corrigió y qué no

V001 interpretaba `raw_source_documents.modified_at` como disponibilidad de
información. En este adaptador SEC el ingestor lo guarda desde el encabezado
HTTP `Last-Modified`. No demuestra una nueva publicación, una revisión económica
ni qué bytes existían históricamente.

La ejecución completa V001 examinó 2.001 estados: 1.886 elegibles y 115 en
cuarentena por vínculos entre accessions. Entre los elegibles, la regla desplazó
169 estados más de un día y 158 más de 30 días; el máximo fue 375,329 días.
El antiguo `integrity_status=PASS` repetía la misma fórmula incorrecta y no
detectaba el error semántico. **V001 no debe usarse para entrenamiento.**
Se preserva como evidencia del fallo, no se elimina ni se vuelve a construir.

V002 reutiliza el motor de agrupación, calendarios, etiquetas y purga mediante
políticas explícitas. No duplica el corpus SEC ni modifica las bases de entrada.
La corrección no es una mejora predictiva medida ni una preregistración de modelo.

## Contrato de relojes

Para admitir una membresía SEC histórica:

- la procedencia debe ser `sec_edgar`, respuesta raw exacta y disponibilidad
  declarada `sec_acceptance_datetime`;
- publicación, disponibilidad raw y disponibilidad de la membresía deben
  coincidir como instantes con la aceptación del filing;
- aceptación, vínculo y semántica deben estar disponibles a más tardar en el
  snapshot; las membresías posteriores se ignoran;
- sólo se admiten las versiones `canonical` / `identical_rerun`; una
  `revision_observed` o procedencia de revisión requiere revisión separada;
- se preservan captura real y observación de versión, sin retrofecharlas;
- si existe `modified_at`, se verifica contra el encabezado HTTP original,
  con zona horaria. Una procedencia desconocida o contradictoria se excluye,
  no se interpreta automáticamente como metadato inocuo.

`Last-Modified` permanece en la procedencia y en un diagnóstico de lo que
habría hecho la regla rechazada. Nunca entra en features ni mueve el origen.
Esto tampoco prueba que una página no haya cambiado antes de nuestra captura.
La identidad histórica de sus bytes sigue sin verificarse: **PIT=0**.

Para los estados admitidos por este contrato:
`A_s = max(relojes legítimos del snapshot y su evidencia) = state_time_s`.
Una frontera posterior al snapshot es una inconsistencia que se excluye;
no se arregla desplazando silenciosamente el estado.

Con retraso adicional `delta`, el origen es el primer cierre bursátil
estrictamente posterior a `A_s + delta`. Se conservan los escenarios
0 / 3.600 / 86.400 segundos. Son sensibilidades, no latencias medidas ni una
regla de decaimiento. No se suman como observaciones independientes.

La primera divulgación pública continúa siendo
`UNKNOWN_EARLIER_DISCLOSURE_POSSIBLE`: el filing puede confirmar algo conocido.
El objetivo sigue siendo la distribución **posterior al cierre elegido**, no
el impacto total, la primera reacción, causalidad ni ejecución a ese precio.

## Cobertura y cuarentenas

Se exige el estado Market Core exacto del activo y cierre. Si falta, el estado
queda documentado en `alignment_audit`; nunca se busca una fecha posterior
para hacerlo entrar en la muestra.

Ejemplo real verificado: BAC del 23/09/2016 a las 12:31:21 UTC vuelve a tener
esa frontera, no octubre de 2017. El cierre candidato base es 23/09/2016, pero
no existe un estado Core exacto para él: queda excluido de las muestras, no
desplazado artificialmente un año. Recuperar cobertura de mercado temprana
sería otra decisión, no parte de esta corrección.

Los vínculos entre filings siguen en cuarentena. Esto no demuestra que sus
documentos sean basura ni autoriza borrar o fusionar identidades. El informe
detalla por estado:

- accession propio y ajenos presentes a esa fecha;
- número de membresías propias/ajenas y métodos de clustering;
- hashes compartidos entre accessions;
- membresías futuras ignoradas;
- concentración por activo y tipo.

Rescatar esos estados exige revisar/reconstruir el vínculo y sus conteos bajo
una versión de estado explícita; no se filtra evidencia del snapshot y se
conservan luego sus features antiguas como si fueran consistentes.

## Auditoría reforzada

Además de reproducir la preparación, un control independiente exige:

- cero desplazamientos injustificados de la frontera;
- completitud de las alineaciones por estado/escenario;
- correspondencia bidireccional entre alineaciones seleccionadas y muestras;
- identidad de evento/filing y frontera iguales a la procedencia;
- corte informativo exactamente igual al máximo declarado, no sólo anterior
  al cierre;
- cobertura por año original del estado, incluyendo años sin muestras;
- concentración de llegadas por activo/fecha;
- pérdida por acciones corporativas en cada horizonte/escenario.

El 10% de exclusión por acciones corporativas dispara una advertencia de
selección; no es un umbral de señal económica ni cambia las etiquetas.
Las exclusiones por acciones corporativas, falta de precios y ventanas
incompletas siguen fuera de los targets utilizables.

Se mantienen las 14 variables propias del Market Core, conteos estructurales
de eventos y separación estricta de features/resultados. Horizonte, calendario,
OHLC, retorno, MFE/MAE y volatilidad poblacional se verifican nuevamente.
Los targets son retorno futuro residual, no total return.

Versiones explícitas:

| Componente | Versión |
|---|---|
| Dataset | `distributional_event_close_aligned_v002` |
| Snapshot SEC de origen | `event_state_v0031_deep` |
| Proyección derivada | `event_arrival_set_v002` |
| Estado de mercado | `market_daily_state_v003_core` |
| Etiqueta Core de origen | `market_daily_reaction_v003_core` |
| Etiqueta derivada | `event_distributional_close_aligned_v002` |
| Ventana final congelada | 2026-08-24 |
| Modelo / folds / semillas / bootstrap | No seleccionados ni ejecutados |

## Qué ejecutar

En la terminal WSL del proyecto:

```bash
cd /home/trabajo/quant_market_ai
.venv/bin/python -m research.events.distributional_dataset_v002 --stage build --run-id sec_core_2016_20260824_v002
```

No requiere esperar al cierre: sólo usa historia hasta 24/08/2026.
**Evitar escrituras/refrescos concurrentes en las bases de origen.** No retrasar
el sellado obligatorio de V009 por este trabajo; ambos procesos son separados.

La ejecución completa queda a cargo del usuario. No descarga, refitea ni abre
artefactos de V009. Un resultado `REVIEW` y código de salida `2` es esperable:
hay limitaciones científicas que no desaparecen al corregir el programa.
`FAIL` / código `1` o una excepción requieren investigar; no entrenar.

La salida nueva es:

```text
reports/distributional_event_dataset_v002/sec_core_2016_20260824_v002/
  AUDIT.md
  audit.json
  manifest.json
  dataset.sqlite
```

Compartir **AUDIT.md y audit.json** para revisar cobertura, tiempos y exclusiones.
Las bases/raw/informes voluminosos siguen fuera de Git; código, configuración,
tests y documentación sí son versionables.

Para verificar posteriormente el resultado sin reconstruirlo:

```bash
.venv/bin/python -m research.events.distributional_dataset_v002 --stage audit --run-id sec_core_2016_20260824_v002
```

Un build repetido sólo reutiliza un resultado completo con los mismos inputs,
configuración y código. Si cambian, o quedó una ejecución incompleta, se exige
otro `--run-id`; no se sobrescribe. El audit verifica el hash de la base y
la concordancia entre manifiesto interno/externo y configuración.

Cada consulta tiene un límite de 30 segundos. Si se agota, investigar y, si
corresponde, ejecutar con `--query-seconds 120` y otro nombre de corrida.
No es un límite de duración del conjunto completo.

Después de la ejecución completa se puede actualizar el contexto local:

```bash
.venv/bin/python tools/project_context.py
```

## Materialización y revisión completas

La ejecución `sec_core_2016_20260824_v002` fue reabierta y reproducida por el
auditor actual:

- integridad `PASS`, estado científico `REVIEW`, fallos: cero;
- 2.001 / 2.001 estados examinados; 1.885 temporalmente elegibles;
- 115 estados cross-accession y un estado AAPL con referencia duplicada al
  mismo archivo quedaron fuera;
- cero fronteras desplazadas de forma injustificada;
- 151 estados elegibles no tienen Market Core exacto: 40 de 2016 y 111 de
  2017; el primer origen seleccionado es 30/08/2017;
- 1.365 / 1.367 / 1.354 muestras para retrasos 0 / 3.600 / 86.400 segundos;
- 4.086 filas de escenario en total, que no son observaciones independientes.

Los 115 clusters cross-accession contienen entre 4 y 10 membresías propias y
ninguno carece de evidencia del accession del evento, pero también contienen
evidencia de uno a tres filings ajenos. Se excluyen completos en V002: rescatar
subconjuntos cambiaría los conteos persistidos y necesita otra versión y una
sensibilidad predeclarada.

El caso AAPL
`est_0dbb22352010a18c682a1aa9b2862cc0cf59574aafca65cf1f9db80d22006962`
no representa dos contenidos: el mismo raw
`5e0f1685b508702089101f903932dc700c1adad01ad3f08572f9c36a2a1883c1`
aparece como documento `primary` y secuencia `1` del mismo accession.
V002 lo conserva en cuarentena para no reescribir un artefacto terminado.

En el escenario base, los outcomes utilizables H1/H3/H5/H10 son
1.351 / 1.315 / 1.249 / 1.032. Las acciones corporativas excluyen
1,0% / 3,6% / 8,4% / 24,3%, respectivamente. H10 requiere una sensibilidad de
selección explícita y no puede convertirse silenciosamente en primario.

Las cinco ventanas H1 heredadas de V008.1 son factibles. Después de purgar
targets solapados y grupos de evento/filing/contenido, los pares
train/test son 395/212, 613/189, 803/180, 983/196 y 1.179/172. Ese tamaño exige
un Event Head pequeño y regularizado; no justifica árboles grandes o redes.

Decisión D033: esta puerta de datos queda cerrada con exclusiones explícitas.
La siguiente puerta es preregistrar el benchmark, todavía sin entrenarlo.

Pruebas rápidas:

```bash
.venv/bin/python -m py_compile research/events/distributional_dataset_v001.py research/events/distributional_dataset_v002.py
.venv/bin/python -m pytest -q tests/test_distributional_event_dataset_v001.py tests/test_distributional_event_dataset_v002.py
```

Antes de entrenar falta revisar la materialización completa y preregistrar el
experimento: folds temporales purgados, grupos evento/filing/contenido, controles
de capacidad, métricas propias de distribuciones y bootstrap dependiente.
El ajuste final V009 no se usa retrospectivamente como control OOS histórico.
No se añade información ni se modifica V009 para rescatar un resultado.

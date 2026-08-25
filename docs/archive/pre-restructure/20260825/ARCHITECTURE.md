# Quant Market AI — Architecture v0.1

## 0. Propósito

Este proyecto busca construir un sistema probabilístico de modelado de mercado que mantenga un estado actualizado de cada activo, aprenda cómo ese estado evoluciona y produzca distribuciones de trayectorias futuras. Las noticias, eventos, macroeconomía y grafos no son un disparador obligatorio de la predicción: son fuentes de información que enriquecen o modifican el estado y la distribución resultante.

El sistema NO debe asumir que el mercado es normal, que la incertidumbre cae linealmente con el tiempo, que una noticia tiene impacto instantáneo, ni que una fuente tiene una confiabilidad fija escrita a mano.

---

## 1. Principios de diseño

1. **El precio observado es la verdad observacional.**
   El sistema puede equivocarse al interpretar una noticia, una relación o una expectativa, pero el retorno realmente observado es el ground truth del entrenamiento y de la evaluación.

2. **Predicción sin noticias.**
   El modelo de mercado debe poder generar una predicción únicamente a partir del historial del activo, mercado, macro y relaciones disponibles.

3. **Las noticias modifican el estado.**
   Una noticia puede alterar retorno esperado, incertidumbre, colas, régimen o cualquier combinación de ellos. No debe ser un activador obligatorio.

4. **Separar observaciones de inferencias.**
   Datos crudos, features derivadas, outputs de IA, predicciones y resultados reales deben almacenarse en capas separadas.

5. **No hardcodear conocimiento económico si puede aprenderse.**
   La confiabilidad de una fuente, el impacto de un tipo de noticia, la fuerza de una relación o la vida útil de un evento deben poder aprenderse de evidencia histórica.

6. **Todo modelo debe ser reproducible.**
   Cada predicción debe identificar versión de modelo, versión de features y estado usado.

7. **El tiempo es una variable del problema, no un multiplicador universal.**
   No se debe imponer `mu_T = mu_1 * T` ni `sigma_T = sigma_1 * sqrt(T)` como ley del sistema.

8. **Walk-forward primero.**
   Toda evaluación económica debe respetar causalidad temporal y evitar leakage.

9. **Las noticias repetidas no equivalen a evidencia independiente.**
   Deben agruparse en eventos/ecos.

10. **El sistema debe aprender de sus errores sin sobrescribir ciegamente producción.**
    Los modelos se versionan, se comparan y sólo un candidato validado pasa a producción.

---

## 2. Flujo general

```text
                    ┌─────────────────────────┐
                    │      DATOS RAW          │
                    │ precios / noticias      │
                    │ macro / eventos         │
                    └────────────┬────────────┘
                                 │
                                 ▼
                  ┌────────────────────────────┐
                  │  NORMALIZACIÓN / ENTIDADES  │
                  └────────────┬───────────────┘
                               │
              ┌────────────────┼────────────────┐
              ▼                ▼                ▼
        ┌──────────┐    ┌────────────┐   ┌────────────┐
        │ MARKET   │    │ NEWS/EVENT │   │ KNOWLEDGE  │
        │ FEATURES │    │ FEATURES   │   │ GRAPH      │
        └────┬─────┘    └─────┬──────┘   └─────┬──────┘
             └────────────────┼────────────────┘
                              ▼
                    ┌────────────────────┐
                    │  MARKET STATE      │
                    │  X(t)              │
                    └──────────┬─────────┘
                               │
              ┌────────────────┴─────────────────┐
              ▼                                  ▼
     ┌──────────────────┐               ┌─────────────────┐
     │ BASE MARKET MODEL│               │ EVENT ADJUSTER  │
     │ P(futuro|estado) │               │ impacto noticia │
     └────────┬─────────┘               └────────┬────────┘
              └────────────────┬─────────────────┘
                               ▼
                    ┌────────────────────┐
                    │ DISTRIBUTION/       │
                    │ TRAJECTORY ENGINE   │
                    └──────────┬─────────┘
                               ▼
                    ┌────────────────────┐
                    │ RISK / DECISION    │
                    └──────────┬─────────┘
                               ▼
                  ┌────────────────────────┐
                  │ REPORT / PAPER TRADING │
                  └────────────┬───────────┘
                               ▼
                    ┌────────────────────┐
                    │ REAL OUTCOME       │
                    └──────────┬─────────┘
                               ▼
                    ┌────────────────────┐
                    │ DIAGNOSTICS/DRIFT  │
                    └──────────┬─────────┘
                               ▼
                    ┌────────────────────┐
                    │ MODEL CANDIDATE    │
                    └────────────────────┘
```

---

## 3. Capas del sistema

### 3.1 Raw data

Responsabilidad: almacenar lo que proviene de fuentes externas sin reinterpretarlo.

- `assets`
- `price_bars`
- `news_documents`
- `macro_observations`
- `scheduled_events`

No deben guardar predicciones de IA.

### 3.2 Knowledge / ontology

Responsabilidad: representar entidades y relaciones.

- `entities`
- `entity_relations`
- `event_entities`
- `learned_relations`

La relación puede ser explícita (ej. propiedad) o aprendida estadísticamente.

### 3.3 Derived state / feature store

Responsabilidad: convertir observaciones en estado cuantitativo reproducible.

- `market_state_snapshots`
- `market_features`
- `news_features`
- `graph_features`
- `event_features`

Las features deben tener timestamp y versión.

### 3.4 Models

Separar al menos:

#### Market model

Aprende la dinámica del precio/retorno a partir del estado de mercado.

Conceptualmente:

`P(trajectory | market_state, horizon)`

#### Event model

Aprende cómo eventos/noticias modifican la distribución base.

No reemplaza al market model.

#### Risk model

Aprende probabilidades de pérdida, MAE, colas y otras medidas de riesgo.

#### Calibration model

Comprueba si probabilidades predichas realmente corresponden a frecuencias observadas.

---

## 4. Predicción: definición matemática provisional

No usar como implementación todavía:

`R_T ~ Normal(mu*T, sigma*sqrt(T))`

En su lugar, el objetivo conceptual es una distribución condicional de trayectorias:

`P(R[t:t+T] | X_t, E_t, G_t, T)`

Donde:

- `X_t`: estado de mercado.
- `E_t`: eventos relevantes conocidos hasta t.
- `G_t`: estado de relaciones/grafo.
- `T`: horizonte elegido.

La implementación puede empezar de forma simple y luego evolucionar.

### Salida mínima recomendada del predictor

Para un horizonte `T`:

- `q05`
- `q25`
- `q50`
- `q75`
- `q95`
- `P(R_T > 0)`
- `P(R_T > cost)`
- `P(R_T < -loss_threshold)`
- `MFE esperado`
- `MAE esperado`

El gráfico de escenarios deberá generarse a partir del modelo, no mediante ruido gaussiano arbitrario.

---

## 5. Tiempo y multi-horizonte

El usuario selecciona un horizonte `T`.

El horizonte no debe ser simplemente un multiplicador posterior de una predicción a 60 minutos.

En la primera implementación práctica se recomienda soportar un conjunto de horizontes entrenables, por ejemplo:

- 5 min
- 15 min
- 30 min
- 1 h
- 2 h
- 1 día
- 3 días
- 1 semana

No se debe asumir que todos los horizontes requieren modelos completamente independientes. Más adelante se puede compartir representación y condicionar por `T`.

---

## 6. Noticias y eventos

Una noticia no equivale necesariamente a un evento único.

Ejemplo:

100 artículos que repiten un mismo anuncio = 1 evento + múltiples evidencias.

Por eso:

`news_documents -> event_cluster -> event -> event_features`

### Variables a aprender, no hardcodear

- confiabilidad de la fuente;
- importancia económica;
- novedad;
- sentimiento textual;
- impacto de mercado;
- alcance empresa/industria/mercado;
- persistencia temporal;
- sorpresa frente a expectativa;
- probabilidad de confirmación/retractación.

FinBERT/BART pueden ser modelos auxiliares, pero sus outputs son evidencia/features, no la verdad final.

---

## 7. Decaimiento temporal de eventos

No imponer una única curva `e^(-lambda*t)` para todo.

Se debe aprender la persistencia a partir de los resultados observados.

Un evento puede:

- perder impacto rápidamente;
- persistir días;
- amplificarse al difundirse;
- cambiar de signo después de una confirmación;
- no mover el retorno esperado pero aumentar la incertidumbre.

---

## 8. Grafo

Se recomienda separar al menos:

1. **Grafo estructural**: ownership, supplier, customer, competitor, regulatory exposure, etc.
2. **Grafo estadístico**: correlaciones, leads/lags y dependencias observadas.
3. **Grafo aprendido**: relaciones no especificadas explícitamente pero respaldadas por evidencia repetida.

Una relación aprendida debe almacenar evidencia, confianza, observaciones, latencia y fecha de última validación.

No usar co-ocurrencia de noticias como causalidad por defecto.

---

## 9. Market state

El predictor base debe funcionar sin noticias.

El estado debe incluir progresivamente:

- retornos multi-escala;
- momentum;
- tendencia;
- volatilidad;
- ATR;
- RSI u otros osciladores;
- volumen;
- drawdown;
- distancia a máximos/mínimos;
- relación con índice/sector;
- variables macro;
- régimen de mercado;
- features derivadas del grafo.

La lista exacta no se considera definitiva.

---

## 10. Tensión / divergencia

No debe implementarse inicialmente como una constante arbitraria.

Se puede representar como una discrepancia aprendida entre variables, por ejemplo:

- expectativa/sentimiento vs precio;
- momentum vs valuation proxies;
- empresa vs sector;
- evento esperado vs reacción observada.

Más adelante se puede aprender una variable latente o un modelo de probabilidad de expansión de volatilidad/breakout.

---

## 11. Decision engine

Separar la predicción de la decisión.

El predictor responde:

> ¿Qué distribución de futuros considero plausible?

El risk model responde:

> ¿Qué probabilidad tengo de sufrir determinado daño?

El decision engine responde:

> Dados retorno, riesgo, costos, horizonte y restricciones, ¿existe una oportunidad?

No usar una fórmula binaria `U*P-D*(1-P)-cost` como representación universal del sistema.

El primer criterio práctico puede ser:

`expected_net_return > 0`

junto con restricciones de probabilidad y riesgo.

---

## 12. Costs

Separar:

- comisión de entrada;
- comisión de salida;
- derechos/impuestos;
- spread;
- slippage.

No hardcodear `1%` dentro del predictor.

Los costos pertenecen al decision/trading layer y deben poder configurarse por broker/activo/escenario.

---

## 13. Predicciones y aprendizaje

Cada predicción debe persistir incluso si nunca genera una operación.

Flujo:

`prediction -> outcome -> error -> diagnosis -> evaluation -> candidate model`

Esto permite aprender de:

- aciertos;
- errores de magnitud;
- errores direccionales;
- mala calibración;
- drift de mercado;
- eventos inesperados;
- problemas de datos.

---

## 14. Continuous learning

No usar “MAE últimas 20 operaciones > 0.5%” como único disparador.

Debe existir:

1. recolección de resultados;
2. evaluación rolling;
3. detección de drift;
4. entrenamiento de candidato;
5. backtest walk-forward;
6. comparación contra producción;
7. promoción si mejora;
8. rollback si empeora.

Los modelos se versionan.

---

## 15. Data leakage: regla crítica

Para una predicción creada en `t`:

- sólo puede usarse información disponible en `t`;
- una noticia con `published_at > t` no existe;
- el futuro no puede entrar en features;
- la agrupación de eventos debe respetar disponibilidad temporal;
- cualquier dato corregido posteriormente debe conservar su timestamp real de disponibilidad cuando sea posible.

Este punto es obligatorio antes de entrenar un modelo serio.

---

## 16. Walk-forward evaluation

Ejemplo conceptual:

```text
TRAIN ----------------| TEST
                     T0

TRAIN ------------------------| TEST
                              T1

TRAIN --------------------------------| TEST
                                     T2
```

El modelo sólo ve pasado al crear cada predicción.

Nunca hacer un `train_test_split` aleatorio sobre series temporales como evaluación principal.

---

## 17. Data quality

Antes de entrenar:

- auditar gaps;
- duplicados;
- timestamps;
- zonas horarias;
- acciones corporativas;
- volumen;
- activos deslistados;
- cambios de ticker;
- cobertura por activo;
- cobertura por fuente.

Yahoo Finance se conservará como fuente histórica inicial, pero no se asumirá que es ground truth perfecto.

---

## 18. Migración desde quant_market_bot

No borrar `quant_market_bot`.

La migración debe ser:

`legacy audit -> mapping -> migration -> validation`

Los datos originales deben mantenerse intactos mientras se valida la nueva base.

Mapeo inicial esperado:

- `universo_tickers` -> `assets`
- `precios` -> `price_bars`
- `noticias` -> `news_documents`
- `macro_diario` -> `macro_observations`
- `relaciones_organicas` -> `entity_relations` o evidencia histórica
- `vectores_estado` -> `market_state_snapshots` / `market_features`
- `correlaciones` -> resultados de evento legacy / dataset de entrenamiento legacy
- `paper_trading` -> `trading_positions` y/o `prediction_outcomes`

La migración de `correlaciones.impacto_mfe_60m_pct` no debe convertirse en el único target nuevo. Se conservará como dato histórico de la versión antigua.

---

## 19. Estructura de carpetas

```text
quant_market_ai/
├── app/
│   ├── api/
│   ├── frontend/
│   └── reports/
├── config/
├── data/
│   ├── database/
│   ├── processed/
│   └── raw/
├── database/
│   ├── migrations/
│   ├── repositories/
│   └── schema.sql
├── evaluation/
│   ├── backtest/
│   ├── diagnostics/
│   ├── drift/
│   └── metrics/
├── features/
│   ├── graph/
│   ├── macro/
│   ├── market/
│   └── news/
├── forecasting/
│   ├── distributions/
│   ├── scenarios/
│   └── trajectories/
├── ingestion/
│   ├── events/
│   ├── macro/
│   ├── news/
│   └── prices/
├── knowledge/
│   ├── entities/
│   ├── graph/
│   ├── learned/
│   └── relations/
├── models/
│   ├── calibration/
│   ├── events/
│   ├── market/
│   ├── registry/
│   └── risk/
├── tests/
├── trading/
│   ├── costs.py
│   ├── decision_engine.py
│   └── paper_trading.py
└── workers/
    ├── inference_worker.py
    ├── learning_worker.py
    ├── macro_worker.py
    ├── news_worker.py
    ├── price_worker.py
    └── state_worker.py
```

---

## 20. Primera implementación recomendada

### Fase A — datos

1. Ejecutar `legacy_audit.py`.
2. Revisar resultados.
3. Crear DB v2 con `database/schema.sql`.
4. Migrar datos crudos y validar conteos.

### Fase B — estado

5. Implementar `price_worker` limpio.
6. Implementar `state_worker`.
7. Crear snapshots reproducibles.

### Fase C — baseline

8. Crear targets multi-horizonte.
9. Entrenar primer market model sin noticias.
10. Medir baseline walk-forward.

### Fase D — eventos

11. Migrar/normalizar noticias.
12. Agrupar eventos.
13. Crear event features.
14. Entrenar event adjustment.

### Fase E — relaciones

15. Construir grafo estructural.
16. Añadir estadística lead/lag.
17. Crear learned relations.

### Fase F — escenarios

18. Construir distribución futura.
19. Generar trayectorias coherentes.
20. Crear bandas/escenarios para la UI.

### Fase G — trading

21. Costs.
22. Risk model.
23. Decision engine.
24. Paper trading.

### Fase H — aprendizaje continuo

25. Predictions/outcomes.
26. Diagnostics.
27. Drift.
28. Candidate models.
29. Promotion/rollback.

---

## 21. Criterio para saber si estamos avanzando

No medir el proyecto sólo por “ganó plata”.

Primero demostrar:

1. datos temporales consistentes;
2. baseline reproducible;
3. predicciones calibradas;
4. distribución de resultados razonable;
5. generalización out-of-sample;
6. mejora incremental por cada nueva fuente de información;
7. sólo después, rentabilidad neta y drawdown.

La pregunta central en cada etapa es:

> ¿Esta nueva pieza agrega información predictiva real fuera de muestra o simplemente hace que el modelo memorice mejor el pasado?

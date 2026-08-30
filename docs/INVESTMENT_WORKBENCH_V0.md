# Investment Workbench V0

Status: implemented product boundary / forecast UX V0.1
Date: 2026-08-29

## Goal

Create the first visible product surface for Quant Market AI without pretending that experimental outputs are validated trading alpha.

V0 is a local, read-only investment workbench. It consumes a stable `InvestmentState` JSON artifact and renders a decision-oriented UI. Scientific databases are never queried by the browser.

The V0.1 forecast UX adds the visual language required by the long-term product vision: a market-like historical chart that crosses an explicit `Ahora` boundary into probabilistic future scenarios, plus a separately published confidence curve that is allowed to move non-monotonically.

## Navigation

The Workbench is split into tabs instead of one research dashboard:

- **Resumen** — asset, decision state, horizons and a compact historical/future chart;
- **Forecast** — large scenario chart, confidence curve, quantile explanation and current distribution;
- **Eventos** — causal/scheduled timeline and event methodology;
- **Riesgo** — tails, evidence and provenance;
- **Journal** — optional user decision snapshot.

## H1 / H5 / H10

`H` means **forecast horizon**, measured in eligible market sessions in the current daily research contract.

- `H1`: cumulative result after 1 market session;
- `H5`: cumulative result after 5 market sessions;
- `H10`: cumulative result after 10 market sessions.

They are **not hours**. A future intraday product may use a different horizon vocabulary and must state it explicitly.

## q05 / q25 / q50 / q75 / q95

The quantiles describe the conditional return distribution at a horizon.

- `q05`: 5th percentile. Roughly 5% of modelled outcomes fall below it;
- `q25`: 25th percentile, used in the UI as a moderate bearish scenario;
- `q50`: 50th percentile / median, used as the central scenario;
- `q75`: 75th percentile, used as a moderate bullish scenario;
- `q95`: 95th percentile. Roughly 5% of modelled outcomes fall above it.

`q50` does **not** mean “50% probability that the stock goes up”. Quantiles are return levels, not class probabilities.

## Forecast trajectory

A horizon endpoint distribution is not enough to justify a zig-zag future chart. Therefore the browser never invents an intermediate path.

A forecast may optionally publish:

```json
"trajectory": {
  "source_kind": "MODEL_OUTPUT",
  "value_semantics": "cumulative_return_pct_from_origin",
  "points": [
    {
      "offset_sessions": 1,
      "date": "...",
      "quantiles": {"q05": -1.0, "q25": -0.3, "q50": 0.1, "q75": 0.5, "q95": 1.2}
    }
  ]
}
```

The chart then renders:

- q50 as the central path;
- q75 as a moderate bullish path;
- q25 as a moderate bearish path;
- q05–q95 as the wider uncertainty fan;
- the historical series on the left of an explicit `Ahora` divider.

When a trajectory is absent, the UI says that only endpoint quantiles are available rather than interpolating a fake market path.

## Confidence curve

Confidence is deliberately separate from direction and from quantile width.

A forecast may publish:

```json
"confidence": {
  "score_semantics": "calibrated_forecast_support_score_0_100",
  "points": [
    {"offset_sessions": 1, "score": 62},
    {"offset_sessions": 2, "score": 68},
    {"offset_sessions": 3, "score": 55}
  ]
}
```

The UI imposes **no monotonicity requirement**. Confidence may rise or fall as the conditional state changes. In a production-grade publisher, the score must be learned/calibrated from historical and prospective evaluation; the UI does not derive it from `sqrt(t)`, horizon length or handcrafted event weights.

A known future event may change confidence, distribution width or both. Knowing that an event will happen does not automatically imply knowing its direction.

## Event markers

Events may additionally publish `scheduled_for`. When a scheduled event overlaps the selected trajectory, the chart marks the nearest session. This is only a temporal marker. It does not apply an event shock unless Event Brain publishes an explicit adjustment.

## Sample mode

`sample_state.json` contains deliberately synthetic normalized history, trajectories, confidence curves and demo scheduled events so that the UX can be tested today.

The sample is visibly marked `MODO DEMO` and **must not be interpreted as an AAPL prediction**. A real publisher must replace these fields with observed history and published model artifacts.

## Architecture

```text
research pipelines
       |
       | publish/validate
       v
InvestmentState JSON  <--- immutable product boundary
       |
       v
stdlib local server
       |
       v
browser UI
```

The server uses only Python standard library modules so V0 does not force a web-framework dependency into the research environment.

## Evidence and decisions

V0 supports:

- `INSUFFICIENT_EVIDENCE`
- `WATCH`
- `RISK_ALERT`

It intentionally rejects executable `BUY_CANDIDATE` / `SELL_CANDIDATE` outputs until a separate decision-policy gate exists.

## Decision Journal

The Journal stores the user stance and note together with the exact SHA-256 of the `InvestmentState` snapshot that was visible. Journal entries never retrain a model automatically.

## Integration strategy

### Stage 0 — implemented

Validate product contract and UX with the explicit demo state.

### Stage 1 — Research → InvestmentState publisher

Publish observed history and actual research artifacts:

- current/observed price and historical series;
- Market V008.1/V009 endpoint quantiles;
- trajectory only if a model actually publishes pathwise quantiles;
- confidence only when a defensible evaluator/publisher exists;
- SEC/event state and scheduled events;
- evidence levels and provenance.

### Stage 2 — Event Brain

Event Brain may publish distributional adjustments, surprise semantics and event-dependent changes to confidence/uncertainty. It must not mutate frozen V009.

### Stage 3 — Decision Engine

Only after an independent decision-policy gate should the product produce trade candidates. Costs, spread, liquidity and portfolio exposure belong here, not inside raw forecast generation.

## Running

```bash
python -m product.workbench_v0.server \
  --state product/workbench_v0/sample_state.json \
  --journal reports/workbench/decision_journal.jsonl
```

Open `http://127.0.0.1:8765`.

## Tests

```bash
python -m unittest tests.test_investment_workbench_v0 -v
```

## V0 non-goals

- live broker connectivity;
- automatic order execution;
- live market-data ingestion;
- refitting models from the UI;
- inventing missing trajectories;
- hardcoding confidence decay;
- graph shock propagation;
- portfolio optimization.

V0 is the first product boundary, not the final trading bot.

## V0.2 temporal contract: horizons are checkpoints, not the final time model

The Workbench contract now supports optional multi-resolution time coordinates.
The existing H1/H5/H10 UX remains unchanged for now; this is an architectural
change, not another UI redesign.

Canonical details live in [`TEMPORAL_FORECAST_ARCHITECTURE.md`](TEMPORAL_FORECAST_ARCHITECTURE.md).

Key rule:

> H1/H3/H5/H10/... remain fixed scientific evaluation anchors. The eventual
> product forecast may be queried at arbitrary trading-time coordinates and may
> combine intraday, daily and long-horizon heads behind one versioned artifact.

`InvestmentState.temporal_contract` can declare:

- exchange calendar and trading-time basis;
- intraday/daily/long-horizon head status;
- fixed evaluation anchors;
- explicit `time_coordinate` for future points.

No path or intermediate forecast is synthesized merely because the contract can
represent it.

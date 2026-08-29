# Next Execution V001 — From Foundation to Real Forecasts

This branch does not modify V009.

## Immediate local sequence

1. Audit temporal data readiness:

```bash
python tools/temporal_readiness_v001.py
```

This writes `reports/temporal_readiness_v001.json` and does not mutate research data.

2. Generate a real-data Workbench state using the actual price DB and Core V003 state:

```bash
python -m product.workbench_v0.publisher_v001 \
  --ticker AAPL \
  --price-db /path/to/market_data.db \
  --output reports/workbench/latest/AAPL.json
```

This publishes real observed history/current state and **does not invent forecasts**.

3. If a validated forecast artifact exists, pass it explicitly:

```bash
python -m product.workbench_v0.publisher_v001 \
  --ticker AAPL \
  --price-db /path/to/market_data.db \
  --forecast-artifact /path/to/validated_forecast.json \
  --output reports/workbench/latest/AAPL.json
```

4. Return `reports/temporal_readiness_v001.json` before implementing new long-horizon labels. The source table/clock must be selected from actual local evidence rather than guessed.

## Next modeling sequence after readiness

### A. Temporal Dataset V001

Materialize terminal-return targets to at least H21/H63/H126/H252 while keeping H1/H3/H5/H10 as existing evaluation anchors. Do not create 252 independent models and do not infer missing horizons by interpolation.

### B. Horizon-conditioned daily model

Train a shared model of the form:

`Q_q(R | X, tau)`

using preregistered horizon sampling. Hold out several horizon distances from model selection to test interpolation/generalization in `tau` rather than visual interpolation of predictions.

### C. Intraday readiness and expansion

Audit actual intraday coverage before new modeling. Existing 60-minute evidence justifies continued investigation but not production promotion. Prefer 15m/30m/60m/to-close anchors; keep 5m diagnostic until data support improves.

### D. Distributional Event Brain

Use the existing `config/distributional_event_brain_v001.json` preregistration. It remains a separate developmental experiment and may not validate, modify or rescue V009.

### E. Coherent Path Brain

Only after temporal marginals are calibrated across scales. A dense collection of marginal quantiles is not yet a coherent path. The first joint model should be a modest multi-target increment/state-space candidate before considering large sequence models.

## Scientific boundary

- H-values are evaluation anchors, not the final time representation.
- Arbitrary horizon queries must come from a learned horizon-conditioned model.
- No Gaussian random-walk path simulation.
- No interpolation between H1 and H10 presented as prediction.
- Reforecasting from a new state is allowed; rewriting old forecasts is not.
- Reforecasting does not imply online retraining.

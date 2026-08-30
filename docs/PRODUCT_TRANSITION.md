# Product Transition — Research to Investment Assistant

Status: canonical product-direction proposal
Date: 2026-08-29

## Purpose

Quant Market AI is not intended to remain a research project. Research exists to decide which components are trustworthy enough to become part of an investment-assistance product.

The project therefore operates on three parallel tracks:

1. **Model track** — validate predictive components out of sample.
2. **Data track** — accumulate causal/PIT information and durable provenance.
3. **Product track** — expose only evidence-backed outputs through an investment workflow.

V009 freezes one scientific claim. It does **not** freeze the project.

## Product principle

A component does not need to prove directional alpha before it can become useful product infrastructure. It must, however, expose its actual evidence level and must never be presented as knowing more than the research supports.

Examples:

- a calibrated volatility/tail forecast can support risk analysis even if directional alpha is absent;
- an SEC event timeline is useful even before Event Brain proves predictive lift;
- a graph can expose verified structural relationships before propagation weights are learned;
- a UI can organize evidence before a BUY/SELL policy exists.

## Long-term system

```text
Observations
    |
    v
Causal Market / Event / Relationship State
    |
    v
Forecast Artifacts
    |
    v
Investment State
    |
    +--> Investor Workbench
    |
    +--> Risk / Decision Engine
    |
    +--> Decision Journal
    |
    v
Outcome Registry
    |
    v
Champion / Challenger Learning Loop
```

Prediction, risk and trading decisions remain separate layers.

## Parallel roadmap

### Track A — Product

1. Investment Workbench V0 (read-only)
2. Decision Journal
3. Paper decision mode
4. Risk + transaction-cost layer
5. Decision Engine
6. Alerts/watchlists
7. Broker adapter
8. Live execution only after explicit independent gates

### Track B — Models

1. V009 prospective validation remains frozen
2. Distributional Event Brain
3. Rich event semantics / surprise / expectations
4. External news
5. Relationship/Graph incremental tests
6. Multi-horizon coherent trajectory model

### Track C — Data

1. Strict-PIT live capture
2. SEC/IR capture
3. Scheduled events and consensus snapshots
4. Macro release vintages
5. Relationship evidence with validity intervals
6. News lineage and provenance

## Evidence ladder

Every product-facing model output MUST carry one of these evidence levels:

- `UNAVAILABLE` — no model output exists.
- `RESEARCH_ONLY` — exploratory; not eligible for investment language.
- `DEVELOPMENTAL` — repeated out-of-sample evidence, but selected on historical data.
- `PROSPECTIVE_PENDING` — frozen prospective experiment accumulating outcomes.
- `PROSPECTIVE_SUPPORTED` — preregistered prospective gate passed.
- `PRODUCTION_CANDIDATE` — prospective support plus stability/operational/risk checks.

The UI must render the evidence level next to the output.

## What V0 may say

Allowed examples:

- "Distribution is wider than the baseline."
- "Tail/shape signal: developmental."
- "Directional edge: not established."
- "SEC filing available at 16:12 ET."
- "Prospective confirmation: pending V009."

Not allowed without evidence:

- "BUY — 83% confidence."
- "Expected return +7.2%" when no validated expected-return model produced it.
- "This filing will move suppliers by 4%" before graph propagation has incremental evidence.

## Separation from V009

The product track may read published/frozen V009 artifacts but must never:

- alter V009 features;
- change the frozen universe;
- refit V009;
- change its calibration;
- modify its eligibility criteria;
- use UI feedback to tune it before the prospective gate closes.

V009 is an experiment. The Workbench is a consumer.

## Definition of success

The transition succeeds when opening one ticker answers, with provenance:

1. What is happening now?
2. What distribution of outcomes does the system currently support?
3. What does it *not* know?
4. What events are relevant and when did they become available?
5. What risks dominate the decision?
6. What evidence level backs every claim?
7. What decision did the user/system record, and what later happened?

The research layer earns components. The product layer composes them.

## Multi-resolution time track

Product transition does not equate `horizon buttons` with the final forecast
architecture. Discrete horizons are retained as falsifiable checkpoints while a
separate multi-resolution time track is developed.

Planned layers:

```text
intraday distributional head
        +
horizon-conditioned daily/long marginal head
        ->
coherent joint path model
```

The old Intraday V002 result is retained as a limited research clue, not a
production dependency. The daily and intraday branches share product contracts
but remain separately validated until enough evidence supports fusion.

See `docs/TEMPORAL_FORECAST_ARCHITECTURE.md`.

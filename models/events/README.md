# Event model — not trained yet

The first model should predict *incremental distributional effects* rather than standalone returns.

Conceptual target:

P(R[t:t+T] | market_state, event_state, horizon)

relative to:

P(R[t:t+T] | market_state, horizon)

Candidate outputs:

- delta median return;
- delta quantile width;
- delta downside tail probability;
- delta upside tail probability;
- event persistence state.

Do not train this model until event clustering, temporal availability, and event reaction targets are validated.

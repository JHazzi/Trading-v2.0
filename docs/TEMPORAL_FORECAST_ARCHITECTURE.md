# Temporal Forecast Architecture V001

**Status:** proposed product/research bridge  
**Date:** 2026-08-29  
**Scope:** multi-resolution time contract; does not modify or rescue frozen V009

## 1. Why this document exists

The current research uses discrete horizons such as H1/H3/H5/H10 because they
are easy to label, purge, benchmark and falsify. That is a sound evaluation
technique, but it is not the intended final representation of time.

The product vision is a forecast that can answer both:

- what may happen inside the current/next trading session; and
- how the plausible market path evolves over days, weeks and months.

Therefore **evaluation anchors and product time are separate concepts**.

Discrete horizons remain checkpoints. The long-term forecast object is a
multi-resolution conditional path distribution.

## 2. Core distinction

### Evaluation anchors

Examples:

```text
5m, 30m, 60m, next close
H1, H3, H5, H10, H21, H63, H126, H252
```

These are fixed places where the system is scored reproducibly.

They answer:

> At this specific future distance, are the predicted probabilities/quantiles calibrated and useful OOS?

### Product time

The user should eventually be able to ask for an arbitrary valid time such as:

```text
45 minutes
2 sessions
7 sessions
17 sessions
43 sessions
6 months
1 year
```

The forecast engine should not require one independently trained model for every
possible session number.

## 3. Proposed architecture

Do **not** build 252 independent daily models.

Use a hierarchy of time-resolution heads behind one contract:

```text
                         CURRENT CAUSAL STATE
                                  |
              +-------------------+-------------------+
              |                   |                   |
              v                   v                   v
        INTRADAY HEAD        DAILY/SWING HEAD    LONG-HORIZON HEAD
       minutes -> close       sessions 1..~63      ~21..252 sessions
              |                   |                   |
              +-------------------+-------------------+
                                  |
                                  v
                    MULTI-RESOLUTION FORECAST
                                  |
                    fixed evaluation checkpoints
```

The boundaries are not permanent truths. They are pragmatic data/model
boundaries and must be selected by evidence.

## 4. Why intraday should exist

Intraday is not unnecessary. Daily close-to-close data collapses several very
different transitions:

```text
close -> overnight -> open -> regular session -> close
```

An 11:30 event, a 16:10 earnings release and a pre-market announcement cannot be
represented faithfully by the same daily timestamp.

The repository already contains positive-but-limited intraday V002 evidence:
its 60-minute pilot improved relative to V001, while the 5-minute result was
much weaker and the total validation window was only about seven sessions.
Therefore intraday is a **research candidate**, not a production-quality head.

Recommended first intraday checkpoints:

```text
5m   diagnostic only initially
15m
30m
60m  strongest existing clue
session close
next open / overnight transition
```

A session-clock feature is mandatory: minute-from-open, minutes-to-close,
regular/pre/post-market state and overnight boundary.

## 5. Continuous-horizon marginals without 252 models

A practical intermediate model is horizon-conditioned:

\[
Q_q(R_{t\rightarrow t+\tau}\mid X_t,\tau)
\]

where `tau` is an explicit continuous/numeric time input in trading time.

Instead of training one model for H1, another for H2, ..., H252, construct
training rows with many target distances and train a shared model that receives
`tau` as a feature.

For example, five quantile heads can share all horizons:

```text
q05(X, tau)
q25(X, tau)
q50(X, tau)
q75(X, tau)
q95(X, tau)
```

Useful horizon features may include deterministic transforms such as
`log1p(trading_minutes)` or `log1p(sessions)`. These describe time; they do not
hardcode market direction or confidence.

Training does not need every possible `tau` for every asset/origin. Use a
predeclared mixture of:

- dense short horizons;
- log-spaced medium/long horizons;
- random horizon sampling inside training only.

Evaluation stays fixed on preregistered anchors.

### Important limitation

A horizon-conditioned marginal model allows arbitrary horizon queries, but
independent marginals do **not** automatically define a coherent future path.
That model is a bridge, not the final trajectory brain.

## 6. Coherent paths

The final target is joint:

\[
P(R_{t+\tau_1},R_{t+\tau_2},...,R_{t+\tau_n}\mid X_t,E_t,G_t)
\]

Intermediate values must share dependence. Otherwise a chart can contain
quantiles that are individually plausible but form an impossible/incoherent
path together.

A realistic development ladder is:

1. **terminal distribution** — current V006/V008.1 family;
2. **horizon-conditioned marginals** — query arbitrary `tau`;
3. **multi-target increment model** — jointly model a modest sequence of
   future increments;
4. **coherent path generator/state-space model** — only after steps 1-3 pass
   temporal OOS and calibration gates.

Do not begin with a large Transformer/diffusion model merely because the final
object is a path.

## 7. Receding-horizon forecasting: how errors are handled

A forecast is immutable evidence about what the system believed at origin `t`.
When new market information arrives at `t+1`, the old forecast is **not edited**.

Instead:

```text
state at t
 -> forecast path A
 -> observe market
 -> score A where outcomes are now known

new state at t+1
 -> forecast path B from the new origin
```

This is receding-horizon / rolling forecasting.

If the market deviates strongly from yesterday's central path, the next origin
naturally rebuilds the entire future distribution using the new observed state.
The deviation becomes evaluation data for model improvement; it does not cause
retrospective rewriting.

This also enables a useful UI overlay later:

```text
yesterday's forecast (faded)
actual path
current updated forecast
```

## 8. Trading time, not naive wall-clock time

A seven-calendar-day period is not seven sessions and contains weekends,
holidays and overnight gaps.

The canonical time coordinate should distinguish:

- exchange trading minutes;
- session index;
- session phase;
- explicit open/close boundaries;
- actual UTC timestamp for lineage.

For a U.S. equity, an intraday forecast should not silently draw a smooth line
through the hours when the regular market is closed. Overnight is a distinct
transition, not missing intraday candles.

## 9. Long horizon to one year

For U.S. equities:

```text
H21   ~ one trading month
H63   ~ one quarter
H126  ~ half a trading year
H252  ~ one trading year
```

These are approximate market-session interpretations, not calendar guarantees.

The product may display a visually continuous curve, but model resolution
should become coarser with distance because minute/day-level precision at one
year is false precision.

Recommended product sampling density:

```text
0 -> 1 session       intraday / session boundaries
1 -> 21 sessions     daily
21 -> 63 sessions    daily or 2-3 session knots
63 -> 252 sessions   weekly-like knots
```

This is a rendering/query strategy, not a hardcoded uncertainty cone.

## 10. Confidence is multidimensional

One scalar `confidence(t)` can be misleading.

At minimum distinguish eventually:

- **distribution calibration**: do nominal quantiles have empirical coverage?;
- **directional calibration**: if P(up)=0.65, does that event occur ~65%?;
- **support/OOD confidence**: is this state represented in training history?;
- **event uncertainty**: do we know an event will happen but not its outcome?;
- **data quality**: are inputs timely and complete?

A scheduled earnings release can simultaneously increase confidence that
volatility will be high while decreasing confidence in direction.

Therefore no monotonic decay is imposed.

## 11. Event Brain integration

The desired object becomes:

\[
P(path\mid X_t,E_t,G_t,\tau)
\]

Event information can alter different parts independently:

```text
location
skew
left tail
right tail
path volatility
jump probability
persistence
```

Do not reduce Event Brain to sentiment -> price shift.

## 12. Evaluation plan

Horizons remain essential **as score checkpoints** even when product time is
continuous.

### Intraday anchors

Start with 15m/30m/60m/session-close and keep 5m diagnostic until data improves.
Evaluate across many independent sessions before promotion.

### Daily/long anchors

Preserve H1/H3/H5/H10, then add H21/H63/H126/H252 under a new frozen dataset
and purging contract.

### Interpolation/generalization tests

For a horizon-conditioned model, deliberately hold out some intermediate
horizons from model selection and test whether querying unseen `tau` values is
calibrated. Otherwise "continuous horizon" is only a UI interpolation trick.

### Path tests

When a joint model exists, score more than endpoints:

- marginal pinball/CRPS by time;
- interval coverage through time;
- realized path volatility;
- MFE/MAE;
- barrier-hit probabilities;
- drawdown distribution;
- path energy/smoothness only as diagnostics, never as a reason to force pretty
  curves.

## 13. Computational strategy

The expensive option is:

```text
497 stocks x 252 horizons x 5 quantile models x repeated refits
```

Do not do that.

Prefer shared horizon-conditioned heads and batch all assets per origin.
Quantile inference for hundreds of assets across a few dozen rendering knots
is cheap relative to model fitting.

For path generation, generate a bounded scenario set once per asset/origin and
cache the immutable artifact; the browser should never run the scientific
model.

## 14. Near-term implementation order

1. Keep V009 frozen and independent.
2. Build the real Research -> InvestmentState publisher for observed history
   and supported terminal forecasts.
3. Run the preregistered Distributional Event Brain track.
4. Create a **Temporal Dataset V001 plan-only** experiment that adds H21/H63/
   H126/H252 and a horizon-conditioned marginal candidate; do not read results
   before freezing the evaluation plan.
5. Audit the old intraday V002 data and ingestion coverage. Expand the number of
   independent sessions before model work; do not train a bigger intraday model
   on seven sessions.
6. Build an intraday distributional baseline around 30/60m and session-close,
   including session-clock/overnight semantics.
7. Only after both time scales have evidence, design the coherent path model.

## 15. Product contract

`InvestmentState` V0 remains backward compatible. It may now optionally publish
`temporal_contract` plus explicit `time_coordinate` objects for forecast/path
points. This allows future intraday and irregular multi-resolution points
without changing the browser-facing product boundary or inventing missing
predictions.

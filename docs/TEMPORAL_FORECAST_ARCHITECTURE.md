# Temporal Forecast Architecture V001

**Status:** proposed product/research bridge  
**Date:** 2026-08-29  
**Scope:** multi-resolution time contract; does not modify or rescue frozen V009

## 1. Why this document exists

The current research uses discrete horizons such as H1/H3/H5/H10 because they are easy to label, purge, benchmark and falsify. That is a sound evaluation technique, but it is not the intended final representation of time.

The product vision is a forecast that can answer both what may happen inside the current/next trading session and how the plausible market path evolves over days, weeks and months.

Therefore **evaluation anchors and product time are separate concepts**.

Discrete horizons remain checkpoints. The long-term forecast object is a multi-resolution conditional path distribution.

## 2. Evaluation anchors vs product time

Evaluation anchors are fixed places where the system is scored reproducibly:

```text
5m, 15m, 30m, 60m, next open, next close
H1, H3, H5, H10, H21, H63, H126, H252
```

The product should eventually support arbitrary valid times such as 45 minutes, 7 sessions, 17 sessions, 43 sessions, six months or one year without requiring an independently trained model for every session number.

## 3. Multi-resolution architecture

Do not build 252 independent daily models. Use a hierarchy of heads behind one forecast contract:

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

The boundaries are pragmatic data/model boundaries, not permanent truths.

## 4. Intraday

Intraday is not unnecessary. Daily close-to-close data collapses close -> overnight -> open -> regular session -> close. An 11:30 event, a 16:10 earnings release and a pre-market announcement should not share one naive time representation.

The existing Intraday V002 result is positive-but-limited: the 60-minute pilot improved versus V001, while 5-minute evidence was weak and validation covered only about seven sessions. Intraday therefore remains a research candidate, not a production head.

Initial checkpoints: 5m diagnostic, 15m, 30m, 60m, session close, next open/overnight. Session-clock features are mandatory.

## 5. Continuous-horizon marginals

A practical bridge is a horizon-conditioned model:

`Q_q(R_{t -> t+tau} | X_t, tau)`

where `tau` is an explicit trading-time input. Five quantile heads can share horizons instead of training H1...H252 independently.

Training need not materialize every tau for every origin. Use a preregistered mixture of dense short horizons, log-spaced medium/long horizons and random horizon sampling inside training. Evaluation remains fixed on preregistered anchors.

**Important:** arbitrary-horizon marginals do not automatically define a coherent path.

## 6. Coherent path ladder

1. terminal distributions — current V006/V008.1 family;
2. horizon-conditioned marginals — arbitrary tau queries;
3. multi-target increment model — jointly model a modest future sequence;
4. coherent path/state-space model — only after earlier stages pass temporal OOS/calibration gates.

Never obtain a path by interpolating H1 and H10 or by drawing a Gaussian random walk around a point forecast.

## 7. Receding-horizon forecasting

Forecasts are immutable. New observations create a new state and a new forecast; they do not rewrite yesterday's prediction.

```text
state at t -> forecast A -> observe -> score A
state at t+1 -> forecast B
```

This lets the system react to mistakes/new information without blind online retraining.

## 8. Trading time

The canonical coordinate distinguishes exchange trading minutes, session index/phase, open/close boundaries and actual UTC timestamps. Overnight is a distinct transition, not a smooth missing segment.

## 9. Long horizon

Approximate U.S. equity anchors:

```text
H21  ~ one trading month
H63  ~ one quarter
H126 ~ half a trading year
H252 ~ one trading year
```

Long-horizon display resolution should become coarser rather than pretending to know every daily wiggle nine months ahead.

## 10. V009 isolation

Nothing in this architecture changes V009's frozen fit, features, universe, prediction sealing, confirmation cohort or evaluation. New temporal work is a separate developmental branch.

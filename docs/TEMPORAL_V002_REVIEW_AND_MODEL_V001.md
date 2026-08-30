# Temporal V002 Economic Review and Distributional Preregistration V001

**Status:** implementation ready for user-run review; no model training.

## Scientific boundary

Market Temporal V002 has already passed its mechanical gates on the real
artifact: 18,573,435 V001 parity comparisons and 4,101,105 no-action identity
comparisons have zero mismatches; all 15,299 in-window action observations
reconcile; every resolved outcome at every materialized tau is usable.

That evidence establishes deterministic construction. It does not by itself
establish that a large special distribution represents the complete economic
entitlement of the pre-event shareholder. This package closes that distinction
without modifying V002.

## Reinvested wealth mathematics

For selected asset session `s`:

```text
g_s = (Close_s + cash_s) / Close_(s-1)
```

and for integer `tau`:

```text
TR(t,tau) = 100 * (product(g_s, s=t+1..t+tau) - 1)
```

Because V002 stores `log(g_s)`, define the prefix:

```text
L_k = sum(log(g_s), s<=k)
```

Then any target in `tau=1..252` is available without a dense outcome table:

```text
TR(t,tau) = 100 * expm1(L_(t+tau) - L_t)
```

The review tests this identity on deterministic nonmaterialized taus while
excluding the five sealed horizon holdouts.

With no cash distribution, the product telescopes to raw close return. With
nonnegative cash, total return cannot be below raw return. These are hard
identities, not plausibility heuristics.

## Special distributions

The review flags every daily cash step at or above 5% of previous close and
marks 10% as critical. A provider adjusted-close match proves timing, units and
share normalization; it does not prove full merger/spin-off/share-exchange
entitlement.

Every flagged step remains visible. A decision is required only if the step can
enter at least one materialized model-visible outcome; pre-origin lineage cannot
block a model it never reaches. Each relevant step receives one external
decision:

```text
validated_cash_and_share_entitlement
quarantine_incomplete_entitlement
```

The decision file is bound to the exact V002 and review-config SHA-256 values,
requires evidence and rationale, and cannot rewrite V002. A quarantine becomes
a downstream versioned selection mask.

## Distribution and support review

Exact gates cover row counts, finite values, the `>-100%` lower bound,
nonnegative cash uplift, zero-cash raw identity and zero action quarantine.
Quantiles are deterministic diagnostic samples; counts, means, extrema and
identity failures are exact.

Cross-horizon claims use an outer-fold clock ending at the last origin where
H252 is resolved. Maximal per-tau support, including the recent right-censored
tail, is reported separately so recency and horizon cannot be confounded.

## Frozen model question

The next model is not 252 independent heads. It asks:

```text
Q_q(total_return | causal own state, tau)
```

against a horizon-and-vol63 control with the same low-capacity HGB profile.
The 12 development anchors are training/model-development data. H7/H17/H42/
H90/H180 remain sealed: they cannot enter fitting, calibration, selection or a
contingency choice. They open once only after the development code, model and
predictions are frozen.

V001 deliberately does not add sampled nonmaterialized taus to training. If
anchor-to-holdout generalization fails, V001 closes. A future V002 experiment
may preregister deterministic sampled taus; it cannot rescue V001 after seeing
holdout performance.

## Dependence

Rows sharing an origin day, asset path or overlapping tau are not independent.
Evaluation first averages across assets within origin day, weights taus
equally on common support and uses whole-day moving blocks of 21/63/126/252
sessions. The 252-session block is primary. Annual-horizon effective sample
size is necessarily small and must be reported.

The supported claim, even after a pass, is only historical developmental
evidence about horizon-conditioned terminal total-return quantiles. It is not
a coherent path, directional alpha, profitability, strict PIT, production or
V009 result.

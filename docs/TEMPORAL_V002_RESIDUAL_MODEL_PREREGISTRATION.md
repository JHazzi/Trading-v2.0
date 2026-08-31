# Temporal Distributional V002 — frozen residual experiment

**Status:** preperformance protocol and runner implementation. No V002 model result yet. V001 is closed negative and its interpolation holdouts remain unopened.

## Scientific question

Does a restricted own-state residual add out-of-sample information to the strong `vol63 + tau` quantile reference without harming H126 or H252?

This is an outcome-informed follow-up to V001: the architecture and fixed half-life were selected after observing V001's developmental failure. It is therefore a new experiment, not a rescue or reinterpretation of V001.

## Exact target and estimator

For total shareholder return in percent, define

\[
z_i=\log(1+R_i/100).
\]

For each outer fold and quantile `q`, the base model uses only causal `asset_vol_63d_pct` and deterministic tau coordinates. Its predictions used to construct residual targets are strictly out-of-fold and past-only:

\[
e_{i,q}=z_i-\widehat Q^{OOF}_{q,base}(z_i\mid vol63_i,\tau_i).
\]

The residual learner predicts the q-specific residual from the frozen endogenous state and tau. A final base is fitted on the complete purged outer-training panel. Test predictions are

\[
\widehat Q_{q,V002}(z\mid X,\tau)
=\widehat Q_{q,base}(z\mid vol63,\tau)
+\alpha(\tau)\widehat Q_q(e_q\mid X,\tau),
\]

with the non-tunable shrinkage

\[
\alpha(\tau)=2^{-(\tau-1)/63},\qquad 1\leq\tau\leq252.
\]

Thus `alpha(1)=1` and `alpha(64)=0.5`. Addition and row-wise monotone rearrangement happen in log-wealth space. Only afterward are quantiles returned to percent:

\[
\widehat Q_q(R)=100\,\operatorname{expm1}(\widehat Q_q(z)).
\]

No outcome clipping or post-model calibration is allowed.

## Causal cross-fitting

Ordinary or shuffled K-fold is forbidden. Within each outer training fold:

1. The first 35% of origin days form the initial causal training window.
2. Remaining days are divided into five contiguous validation blocks.
3. For each block, the base may train only on rows whose origin is earlier and whose `target_trading_day` is strictly earlier than the first validation origin day.
4. The initial burn-in has no OOF prediction. It remains available for the final base fit but is excluded from residual fitting.
5. OOF means out-of-fold; it is not described as unbiased.

Every origin day receives equal total fitting weight, followed by equal weight for each origin/state and each selected tau.

## Evaluation and stopping gates

The outer five purged expanding folds, twelve development anchors and sealed horizons H7/H17/H42/H90/H180 are unchanged from V001. Holdout labels cannot be loaded by a development command.

Before opening holdouts, every frozen development condition must pass, including:

- positive lower bound of the 252-day moving-block interval versus the base;
- at least 8 positive development anchors and 4 positive outer folds;
- at least 3 improved quantiles;
- calibration no worse than the base;
- superiority to the mean placebo by interval and to every placebo seed by point estimate;
- point delta `reference loss - candidate loss >= 0` separately at H126 and H252.

If development fails decisively, V002 is closed negative. If any auxiliary condition fails, the result is inconclusive and holdouts remain sealed. There is no parameter contingency.

After a development pass, code, configuration, protocol, models, predictions and reports are hash-frozen. The five holdouts may then be opened once. At least four must improve and H180 may not be worse by point estimate.

## Tau and claim boundary

The dataset supports outcomes for any integer tau through prefix-factor differences without dense 275-million-row materialization. V002 nevertheless trains only on the twelve declared anchors. Because HGB is stepwise in its tau coordinates, V002 makes no claim of smooth interpolation at unseen horizons. Holdout horizons test some interpolation, but they do not establish a coherent path distribution.

The result is limited to historical marginal terminal total-return quantiles in the current 497-asset cohort. It is not strict PIT, survivorship-free, a trading strategy, alpha, profitability, a coherent trajectory or evidence about V009.

## Canonical files

- Scientific contract: `config/temporal_distributional_preregistration_v002.json`.
- Minimal hash-bound execution contract: `config/temporal_distributional_runner_v002.json`.
- Plan auditor: `tools/temporal_distributional_preregistration_v002.py`.
- Runner: `pipeline/market_temporal_distributional_v002.py`.
- Model mathematics: `models/market/temporal_distributional_v002.py`.
- Gates: `evaluation/market/temporal_distributional_v002.py`.

# Temporal V002: Residual Model Preregistration (Agent Exploratory Track)

**Disclaimer**: This experiment is designed by an AI agent newly onboarded to the `quant_market_ai` project. It constitutes a somewhat separate, exploratory developmental track, formulated directly in response to the explicit post-mortem scientific failure of the V001 Temporal Distributional model. It follows the project's causal and rigorous scientific rules but represents an exogenous design proposal.

## 1. Motivation and Hypothesis

The previous V001 model (which attempted to learn a direct shared representation for `Q_q(R | X, tau)`) failed robustamente against the parsimonious `vol63 + tau` reference. It suffered significantly in central quantiles (q50) and long horizons (H126, H252), confirming that a naive unified capacity diluted the strong long-memory empirical scale baseline.

**Hypothesis**: The conditional total-return distribution can be modeled effectively by anchoring to a strong volatility-based prior and learning a restricted, highly regularized residual correction for specific state-dependent deviations.

\[
Q_q(R \mid X, \tau) = Q_{q,\text{ref}}(R \mid \text{vol63}, \tau) + \Delta_q(X, \tau)
\]

By framing the learning task as predicting $\Delta_q$, the model cannot trivially dilute the base distribution. 

## 2. Methodology

### 2.1. Base Reference Cross-fitting
To avoid *residual leakage* (where the base model overfits the training set, leaving a skewed residual for the secondary model), the base reference $Q_{q,\text{ref}}$ will be evaluated on the training set using out-of-fold predictions (*cross-fitting*). 

- Train data will be split into 5 internal k-folds.
- The base model (using only `vol63` and $\tau$) is fit on 4 internal folds and predicts on the 1 out-of-fold.
- This creates an unbiased base prediction $Q_{q,\text{ref}}$ for the entire training set.
- The residual target becomes: $y_{\text{residual}, q} = y_{\text{actual}} - Q_{q,\text{ref}}$.
- Note: Quantile residuals are not strictly additive in expectations, but in quantile regression, the loss is the pinball loss on $(y_{\text{actual}} - Q_{q,\text{ref}})$.

### 2.2. Residual Learning with Horizon-dependent Shrinkage
The residual learner $\Delta_q(X, \tau)$ will be a HistGradientBoostingRegressor per quantile.
To prevent the degradation seen in V001 at H126 and H252, we will introduce a horizon-dependent regularization mechanism:
- Predictions for longer horizons will be explicitly shrunk towards 0.
- $\Delta_q(X, \tau)_{\text{final}} = \alpha(\tau) \cdot \Delta_q(X, \tau)_{\text{raw}}$
- Where $\alpha(\tau)$ decays from 1.0 at H1 to near 0.0 at H252.

### 2.3. Holdouts and Anchor Selection
- Holdouts (H7, H17, H42, H90, H180) remain strictly sealed and excluded from all training and validation.
- The 12 anchor horizons remain the evaluation basis.
- The exact same row-wise purge and cyclic offset rules from V001 will be used to ensure comparable capacity and prevent label overlap concentration.

## 3. Evaluation Gates

This experiment must pass the following gates before any holdout is opened:
1. **No-Harm Long Horizon**: The performance on H126 and H252 must not be worse than the base `vol63` reference (a direct failure point of V001).
2. **Improved Quantiles**: Must improve at least 3 of 5 quantiles vs the reference.
3. **Placebo Superiority**: Must beat the mean of 5 placebo (deranged-feature) models in a moving block bootstrap CI.

If this protocol fails these gates, we formally declare that endogenous `own-state` capacity scaling has reached its limit, and we must transition to exogenous event-driven datasets.

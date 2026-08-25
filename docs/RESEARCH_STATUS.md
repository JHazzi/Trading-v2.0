# Research Status — 2026-08-25

**Status:** canonical empirical checkpoint  
**Scope:** research, not production trading

## 1. Executive summary

The project has completed the first serious deep SEC Event Brain research corpus.

The data/lineage infrastructure is now substantially stronger than the predictive models.

Current high-level conclusion:

> The SEC Event State contains a **weak candidate incremental signal around 10 sessions**, but this is not statistically confirmed and the current daily Market Brain remains too weak. The next work is robustness/falsification and a stronger market base distribution, not more SEC data or a larger model.

## 2. Market data / Market Brain

### Intraday Market V002

The previously frozen intraday V002 baseline improved the 60-minute pilot relative to V001:

- paired rows: 131,361;
- V001 MAE: `0.466421849`;
- V002 MAE: `0.448511859`;
- relative MAE improvement: about `3.84%`;
- directional accuracy: about `0.5270 → 0.5653`;
- paired bootstrap MAE delta: about `+0.01781`;
- 95% interval: about `[+0.00868, +0.02854]`.

The 5-minute gain was much weaker and directional uncertainty crossed zero.

Important limitation: this intraday validation covered only about seven sessions and is not production evidence.

### Daily market context used by Event Brain

Current daily market features include asset returns 1/3/5/10/20, volatility 5/20, range, distance features, volume ratio, leave-one-out cross-section and leave-one-out sector context.

It does not yet contain a mature broad-market/macro/regime representation.

Deep Event benchmark trivial-zero vs Market MAE:

| Horizon | Zero MAE | Market MAE | Market vs zero |
|---:|---:|---:|---|
| 1 | 1.8725 | 1.9482 | worse |
| 3 | 2.5308 | 2.5379 | roughly equal / worse |
| 5 | 3.2173 | 3.1772 | slightly better |
| 10 | 4.2185 | 4.3244 | worse |

Conclusion: **Market Brain Daily is the biggest predictive weakness.**

## 3. Deep SEC corpus V003.1

Research cohort:

```text
AAPL MSFT JPM BAC XOM CVX LLY JNJ WMT COST
```

Scientific common window:

```text
2016-09-23 → 2026-08-24
```

Persisted scale:

- 1,704 cohort-eligible filings;
- 10,642 persisted evidence semantics / cohort raw documents;
- 1,939 unique normalized events;
- 305 reused stable event identities;
- 1,634 new event identities;
- 2,001 Event States;
- 10 assets;
- 0 strict-PIT states/observations in the reconstructed historical corpus.

The corpus explicitly remains a **historical research reconstruction**.

## 4. Event State distribution

States are broadly distributed rather than concentrated only in recent history:

| Year | States |
|---:|---:|
| 2016 | 40 |
| 2017 | 172 |
| 2018 | 157 |
| 2019 | 209 |
| 2020 | 221 |
| 2021 | 221 |
| 2022 | 220 |
| 2023 | 204 |
| 2024 | 209 |
| 2025 | 208 |
| 2026 | 140 |

JPM event coverage begins in 2019 under the current metadata-depth cap; the market price context for the cohort still begins at the common window.

Largest event categories:

- financial results disclosure: 409;
- other material disclosure: 403;
- quarterly report disclosure: 292;
- Regulation FD disclosure: 246;
- management/board change: 234.

## 5. Reaction labels V003.1

2,001 states × 4 horizons = 8,004 labels.

Status totals:

- usable: 6,343;
- corporate-action overlap: 495;
- intraday/daily-resolution mismatch: 1,164;
- insufficient future sessions: 2.

Usable label counts:

| Horizon | Usable labels |
|---:|---:|
| 1 | 1,701 |
| 3 | 1,668 |
| 5 | 1,620 |
| 10 | 1,354 |

The model-ready loader has one fewer row at each horizon due to its complete-feature contract.

## 6. Deep model-ready datasets

| Horizon | Rows | Unique events | Unique origin days | Event types |
|---:|---:|---:|---:|---:|
| 1 | 1,700 | 1,650 | 974 | 21 |
| 3 | 1,667 | 1,620 | 957 | 21 |
| 5 | 1,619 | 1,573 | 932 | 21 |
| 10 | 1,353 | 1,314 | 849 | 19 |

No sector fallback rows are present in these datasets.

## 7. Event Brain V0.2 pilot

The small recent pilot produced no convincing incremental event evidence at 1/3/5 sessions.

At H10 the capacity-controlled comparison was approximately:

- MAE delta baseline−contextual: `+0.062 pp`;
- bootstrap 95% interval roughly `[-0.006, +0.133]`;
- win rate roughly `57%`;
- only 2 of 4 folds positive.

This was treated as a preliminary hypothesis, not a conclusion.

## 8. Deep replication — same V0.2 training logic on V003.1

The deep runner explicitly fixes:

```text
event_feature_version  = event_state_v0031_deep
label_version          = event_reaction_daily_v0031_deep
market_feature_version = daily_asset_cross_section_sector_v002_leave_one_out
```

Primary incremental comparison:

```text
capacity-control residual
vs
contextual residual (market + event features)
```

Results:

| Horizon | MAE delta control−contextual | Interpretation |
|---:|---:|---|
| 1 | +0.00427 pp | effectively zero |
| 3 | −0.00377 pp | effectively zero / slightly worse |
| 5 | −0.01359 pp | slightly worse |
| 10 | **+0.02819 pp** | weak positive candidate |

H10:

- pooled OOS rows: 778;
- MAE control: `4.76328%`;
- MAE contextual: `4.73509%`;
- relative MAE reduction vs capacity control: about `0.59%`;
- paired bootstrap 95% interval: `[-0.00366, +0.05968]`;
- candidate absolute-error win rate: `0.5064`;
- directional accuracy delta: essentially zero;
- **all 4 H10 folds have lower contextual MAE than capacity-control MAE**.

Interpretation:

> H10 became smaller in magnitude than the pilot but more temporally consistent. The interval still crosses zero, so the effect is not confirmed.

## 9. Important negative result

The simple `market + event` model is worse than `market` at all tested horizons.

At H10:

- Market MAE: `4.32442%`;
- Market+Event MAE: `4.57305%`;
- paired MAE delta: `−0.24862 pp`;
- bootstrap interval remains negative.

Therefore the current event representation cannot simply be added to the market predictor and assumed useful.

## 10. Why the current Event Brain test is incomplete

The current target is primarily `return_pct`.

But the architecture expects events to potentially affect uncertainty, downside/upside tails, path volatility, MFE, MAE and regime probability.

The label system already stores `mfe_pct`, `mae_pct` and `realized_path_vol_pct`; these are not yet modeled.

Therefore:

> “event features do not improve point-return MAE at H1/H3/H5” does **not** imply that events contain no useful distributional information.

## 11. Known scientific limitations

- Current 10-company cohort is not survivorship-free.
- Historical SEC evidence is reconstructed and PIT=0.
- OOS folds begin around 2021 because earlier history forms training/warmup.
- Multiple events can come from the same accession/filing.
- Multiple states can represent one event as evidence evolves.
- Multi-session targets overlap in calendar time.
- Current bootstrap resamples origin days, not horizon-aware multi-day blocks.
- Sector context is thin.
- Daily Market Brain is weak.
- Event representation is taxonomic/structural, not full semantic surprise.
- Expectations/consensus/guidance novelty are absent.
- Corporate-action exclusions remove a meaningful fraction of long-horizon labels.
- No production transaction-cost/risk validation exists.

## 12. Current research claim

Allowed:

> In the current 10-company historical reconstruction, factual SEC event-state features show a weak candidate incremental MAE improvement at approximately 10 sessions relative to a capacity-matched residual control. The effect is positive across four folds but its bootstrap interval still crosses zero.

Not allowed:

- “Event Brain is profitable.”
- “SEC events predict stock direction.”
- “The effect is statistically proven.”
- “The result generalizes to the US equity market.”
- “The historical data are strict PIT.”
- “The current model outputs a calibrated future distribution.”

## 13. Current next step

No more SEC scaling.

Next:

1. Event Brain V0.2.1 robustness/falsification.
2. Market Brain Daily V003.
3. Distributional modeling.

See `ROADMAP.md`.

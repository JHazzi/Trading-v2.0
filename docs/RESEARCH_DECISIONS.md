# Research Decisions

This document records durable research choices and separates decisions made **before** results from interpretations made **after** results.

It is not a package changelog.

## D001 — Target object is a trajectory distribution

**Decision:** the long-term target is a distribution of future paths, not only terminal price or MFE.

\[
P(R_{t:t+T}\mid X_t,E_t,G_t,T)
\]

**Reason:** terminal return can hide economically important intermediate upside/downside and uncertainty.

**Status:** active.

## D002 — Horizon is learned, not extrapolated by a universal formula

Do not use fixed rules such as:

```text
mu_T = mu_1 * T
sigma_T = sigma_1 * sqrt(T)
```

as the system law.

Discrete horizons are acceptable research approximations.

**Status:** active.

## D003 — Market Brain must exist independently of news/events

The base model must forecast from market context alone.

Event Brain is evaluated for incremental information relative to that base.

**Status:** active.

## D004 — Documents are evidence, not events

Duplicate articles/filing resources do not become independent shocks.

Evidence must be clustered/normalized into stable event identities.

**Status:** implemented for SEC research path.

## D005 — Do not hardcode predictive economic meaning

Reliability, importance, direction, persistence and graph strength are learned/contextual quantities.

Deterministic rules may enforce identity, taxonomy, form selection and data integrity.

**Status:** active.

## D006 — `available_at` is the feature boundary

Prediction at `t` may use only information legitimately available by `t`.

Historical reconstruction must preserve the distinction between historical public-availability proxy and actual later retrieval.

**Status:** implemented; current deep SEC corpus is explicitly PIT=0.

## D007 — Stable event identities survive deeper rebuilds

Deep historical scaling must reuse economic identity when the underlying event is the same.

New observations/states use new feature versions when evidence completeness changes.

**Status:** implemented.

## D008 — Common scientific cohort window

For cross-sectional/sector market context, the event research cohort begins only when all required target assets have sufficient market-history readiness.

Current common start:

```text
2016-09-23
```

Old AAPL data remain stored but are excluded from the V003.1 scientific cohort before model-state construction.

**Status:** implemented.

## D009 — Form selection is an experiment boundary

Current deep SEC experiment uses:

```text
8-K, 8-K/A, 10-Q, 10-Q/A, 10-K, 10-K/A
```

Legacy Form 4 documents may remain in storage/clustering infrastructure but are not allowed to create V003.1 experiment events.

**Status:** implemented.

## D010 — Deep scaling test keeps model logic frozen

**Pre-result decision:** evaluate the same Event Brain V0.2 training architecture on the deep V003.1 corpus before changing models.

**Reason:** isolate the effect of data depth.

**Result:** H1/H3/H5 remain near zero/negative; H10 remains weakly positive.

**Status:** completed.

## D011 — H10 was the pre-specified candidate horizon

Before deep replication, H10 was identified as the only pilot horizon with a plausible incremental event signal.

The deep run tested H10 first.

**Result:** H10 control−contextual MAE delta `+0.02819 pp`, all four folds positive, bootstrap interval still crosses zero.

**Interpretation:** candidate survives weakly; no confirmation.

## D012 — Stop SEC scaling after V003.1 checkpoint

**Post-result decision:** current SEC corpus is large enough for the next scientific question.

Do not add more filings/assets merely to increase row count before testing robustness and improving Market Brain.

**Reason:** predictive bottleneck has shifted from event sample size to model/state quality and statistical robustness.

## D013 — Robustness objective is falsification

Event Brain V0.2.1 should try to **destroy** the H10 candidate.

Planned checks:

- simple constant baselines;
- train mean/median;
- always-up/down and train-majority direction;
- Ridge/ElasticNet/Huber;
- multiple RF seeds;
- filing/accession grouping;
- horizon-aware block bootstrap;
- early-OOS sensitivity;
- asset/type concentration;
- extreme-day sensitivity.

Do not tune this stage to maximize H10.

## D014 — Market Brain Daily is higher priority than richer Event Brain

**Post-result decision:** after robustness, improve the base market context.

Required direction includes strictly-as-of broad-market/sector/rate/volatility/regime features.

Target gate:

> Market Brain should consistently beat trivial zero/median baselines OOS before event complexity is treated as the main bottleneck.

## D015 — Move from scalar to distributional evaluation

The current Event Brain point-return MAE experiment is only a minimal information test.

Future research must evaluate whether events improve quantiles, probability forecasts, tail risk, path volatility and MFE/MAE.

Proper scoring rules should replace “tree dispersion = uncertainty”.

## D016 — Rich semantics come after distributional/base-model work

Do not add full text, expectations, guidance surprise, general news and graph propagation until the simpler base/distributional questions are understood.

This prevents new data sources from hiding weaknesses in the base model.

## D017 — Prediction remains separate from trading decision

Costs, broker settings, slippage, risk limits and position sizing belong downstream.

No current research metric should be described as a trade recommendation.

## D018 — Historical experiment artifacts are preserved

Do not delete old model/report/database experiment history simply because a newer version exists.

Documentation may be archived away from the root, but scientific lineage remains reproducible.


<!-- EVENT_T0_V001_START -->
## D020 — First public evidence, not SEC acceptance, defines event information t0

**Decision:** do not equate SEC filing acceptance with the first time the
market could have known an event.

Future multi-source normalization will distinguish:

```text
event_time
first_public_at
source published/accepted time
system observed/retrieved time
feature available_at
```

SEC remains the first authoritative event corpus and a high-quality anchor
source. Investor Relations, press-release wires, official channels, media,
calls/webcasts and macro authorities may reveal information earlier depending
on the event.

A later authoritative confirmation enriches the Event State from that point
forward; it does not retroactively rewrite the earlier information set.

**Reason:** event-return research is invalid if `t0` is placed after the
market had already received the information.

**Status:** active architecture contract.

## D021 — Market Brain Daily V003 is independent of event occurrence

**Decision:** train the daily Market Brain on all eligible asset-days at
session close, not only event-origin rows.

Event Brain integration will later use only the latest Market Brain
prediction/state whose market timestamp is no later than the event-state
timestamp.

**Reason:** the base model must estimate `P(Y|X,T)` independently before
testing the incremental information in `E`.

**Status:** Market Daily V003 foundation.
<!-- EVENT_T0_V001_END -->

## D019 — Documentation has canonical vs historical layers

Canonical docs describe current truth.

Package/fix READMEs are allowed but live under `docs/package-notes/` and are eventually archived.

No future ZIP should pollute repository root with competing `README_*` status documents.

**Status:** introduced by documentation restructure V001.
<!-- MARKET_V003_CORE_H1_FIX_V001 -->
## Market Daily V003 Core H1 label correction

The first Core audit was superseded because it allowed all H1 labels to be
`insufficient_future`.

Cause: H1 path volatility used sample std (`ddof=1`) on a one-return path.

Decision:

```text
path volatility := population std (ddof=0)
H1 path volatility := 0
```

The audit now treats missing/substantially unusable horizons as hard failures.
The processed Core DB is rebuilt from source observations rather than patched
in place.
<!-- MARKET_V003_RESULTS_V004_FACTORIZATION_V001 -->
## D022 — Factorize Market Brain before adding more context

Market V003 demonstrated that pooling own-asset, market-day and sector-day
signals into one asset-day nonlinear model is not currently robust.

Next architecture hypothesis:

```text
Market factor model
    unit = day
        +
Sector residual model
    unit = sector-day
        +
Asset residual model
    unit = asset-day
```

with exact target identity:

```text
asset return
= market factor
+ sector factor
+ asset residual
```

This is a hypothesis to test, not an established explanation of the V003
failure.

Before training V004:

1. quantify common-market and sector target variance;
2. quantify feature replication/topology by statistical unit;
3. freeze the factorized target contract;
4. materialize each level separately;
5. benchmark each component before recombination.

Do not yet add external market proxies, macro, Event Brain, distributional
outputs, or tune V003 after observing its benchmark.
<!-- MARKET_V004_MATH_FOUNDATION_V001 -->
## Decision — preserve V003 and expand Market Brain information carefully

V003 answered a deliberately narrow question: price/volume/relative state
alone, pooled at asset-day level, did not beat the preregistered absolute
return baseline.

This does not reject the project objective
`P(R[t:t+T] | X_t, E_t, G_t, T)`.

Decision:

1. retain all V003 artifacts/results as negative evidence;
2. test factorized mathematical targets without new external data;
3. if factorized components generalize, add external market-wide state
   incrementally to the statistically appropriate level;
4. require causal `available_at <= t` contracts for every enrichment;
5. do not tune V003 after observing its failure;
6. do not integrate Event Brain or distributional heads before a credible
   point-estimate Market Brain baseline exists.
<!-- MARKET_V004_RESULTS_V005_EXTERNAL_STATE -->
## Market V004 factorized result and V005 external-state decision

V004 factorization materially improves V003 but does not pass the preregistered
absolute-return primary against fold train median at H1/H3/H5/H10. The loss
survives moving-block bootstrap.

Decision:

- retain V003 and V004 as canonical evidence;
- stop additional endogenous-price factorization as the primary research path;
- begin V005 incremental Market State enrichment;
- first increment is SPY/QQQ/IWM only;
- sector ETFs, volatility/rates/credit, macro, events and distributional heads
  remain separate later increments;
- no post-result tuning of V004.

This remains Architecture Phase C: improve the base Market Brain without news
before Event Brain integration.
<!-- MARKET_V0052_FINANCIAL_CONDITIONS_V001 -->
## Market Brain V005.2 — financial conditions

V005.1 SPY/QQQ/IWM did not pass its preregistered incremental gate over V004.
It is retained as negative/inconclusive evidence and is not stacked into the
next primary candidate.

V005.2 tests a more orthogonal Market State block:

```text
volatility: Cboe VIX (previous session only)
rates:      SHY / IEF / TLT
credit:     HYG / LQD
```

The V004 factorized Market Brain remains the frozen control.

Causal clock:
- same-day ETF closes are available at the equity-session origin;
- same-day daily VIX close is NOT used because Cboe VIX RTH continues after
  the equity close; VIX features use one full session lag;
- historical reference data remains research backfill, strict PIT=false;
- Yahoo adjusted_close is not used for ETF state features;
- ETF returns are reconstructed from Close plus only cash distributions whose
  effective trading day has occurred, then compounded over 5/20 sessions.

Primary candidate is the complete financial-conditions block. VIX-only,
rates-only and credit-only candidates are preregistered diagnostics and cannot
rescue a failed primary after results.

No sector ETFs, macro vintages, Event Brain, graph, distributional heads,
regime-conditioned training or hyperparameter tuning enter V005.2.
<!-- EVENT_GRAPH_BRAIN_FOUNDATION_V001 -->
## Event–Graph Brain Foundation V001

Market Brain V004 is retained as the frozen structural prior/control. V005.1
and V005.2 remain evidence about market-context information and are not stacked
into Event–Graph Brain.

The next architecture work resumes phases D/E:

```text
evidence -> event -> entity
relation evidence -> temporal structural graph
event + G_t -> asset exposure candidates
```

New canonical contract: `docs/EVENT_GRAPH_CONTRACTS.md`.

Foundation rules:

- candidate extraction is not model-visible until resolution/promotion;
- structural relation evidence must satisfy `available_at <= t`;
- graph propagation nominates potentially exposed assets but assigns no market
  direction or predictive weight;
- structural graph is first; statistical/learned graph and GNN are deferred;
- foundation propagation is one hop;
- evaluation is nested:
  `V004+direct event vs V004`, then
  `V004+direct event+graph vs V004+direct event`;
- graph claims require negative controls, including matched unconnected assets
  and future-evidence leakage checks.

No Event–Graph predictive model is trained in the foundation package.

## D023 — Scalar location failure does not veto distributional scale research

**Decision:** the V003–V005 scalar absolute-return failures remain valid negative evidence, but they do not require the project to postpone every distributional test until a point model beats the median.

**Reason:** conditional location and conditional dispersion are different statistical objects. A causal state variable may improve a proper distributional score and calibration even when it adds no median-return information.

This supersedes only the earlier sequencing clause that required a credible point-estimate Market Brain before distributional heads. It does not weaken the need for trivial baselines, purged walk-forward evaluation or preregistration.

**Status:** active after V006.

## D024 — V006 empirical volatility scaling is the required distributional control

**Decision:** retain the train-only empirical distribution rescaled by causal 20-session asset volatility as the first Distributional Market Brain control.

Evidence:

- positive origin-day-equal pinball delta at H1/H3/H5/H10;
- 5/10/20-day moving-block intervals exclude zero at all horizons;
- calibrated pooled central interval coverage;
- no improvement in median MAE or positive-return Brier.

Consequently, the supported increment is distribution scale. Any learned distributional model must show OOS incremental information beyond V006, not merely beyond an unconditional quantile baseline. V006 is not a production model and is not evidence of directional or tradable alpha.

**Status:** active.

## D025 — Identity conflict candidates cannot mutate canonical entities automatically

**Decision:** Identity Resolution Foundation V001 remains a review artifact.

The 28 conflict groups, 30 pairs and 3 row-quality candidates are inputs to upstream hygiene review. They do not authorize automatic merge, split, exclusion, jurisdiction writeback, canonical-entity creation or graph-edge promotion.

**Status:** active; graph promotion blocked pending review and rebuild.

## D026 — Product confidence and “psychology” have testable semantics

**Decision:**

- “investor psychology” is represented only through observable proxies or learned latent state with OOS evidence;
- market price/path is the observed outcome target; intrinsic value is latent and would require a separate explicit model;
- confidence shown to a user means calibrated probability/coverage at the requested horizon;
- scheduled future events may alter uncertainty without an assumed sign;
- a future path chart requires a coherent joint trajectory distribution, not independent horizon samples;
- automated improvement uses candidate evaluation, promotion and rollback rather than blind model mutation.

**Status:** canonical product interpretation.


<!-- MARKET_DIST_V0061_ROBUSTNESS_V001_START -->
## Decision — freeze V006.1 as falsification, not optimization

V006.1 preserves the completed V006 primary and asks where its conditional-dispersion claim does or does not hold. The experiment must reproduce frozen V006 daily OOS losses before any subgroup diagnostic is accepted. `asset_vol_5d_pct` and `asset_vol_63d_pct` are predeclared sensitivity scales only; neither can become the new primary from V006.1 results. The learned distributional model will receive its own version, preregistration and temporal selection design.
<!-- MARKET_DIST_V0061_ROBUSTNESS_V001_END -->

<!-- MARKET_DIST_V007_ADAPTIVE_TAIL_V001_START -->
## Decision — learn shape/scale before direction

V006.1 showed three coherent facts: longer volatility memory outperforms short memory, a linear scale response miscalibrates low/high volatility regimes in opposite directions, and upside/downside tails behave differently. Therefore the next learned Market Brain will not add directional features or a generic black-box model.

V007 freezes the location at the global training median and learns only tail geometry. It combines an asset-specific empirical tail anchor with a train-normalized blend of vol20 and vol63, allowing separate downside/upside `alpha`, `lambda20` and `kappa` selected only inside each outer training period. This is deliberately more interpretable than jumping directly to a large quantile booster.

The strongest simple V006.1 sensitivity, `vol63_scaled_empirical`, becomes V007's primary reference prospectively. This does not rewrite the completed V006 primary.
<!-- MARKET_DIST_V007_ADAPTIVE_TAIL_V001_END -->

<!-- MARKET_DIST_V007_ZERO_VOL_AMENDMENT_V0011_START -->
## V007 pre-performance implementation amendment — exact zero volatility

The first V007 H1 benchmark attempt aborted during data loading before any OOS metric was produced. Core V003 permits exact zero rolling volatility, while the initial V007 loader incorrectly required both vol20 and vol63 to be strictly positive.

V007.0.1 corrects only this domain handling. Exact observed zero volatility is mapped to the already-frozen lower log-ratio clip; a nonpositive per-asset TRAIN median normalizer falls back to the positive global TRAIN median. The vol20 control restores the completed V006 `global_empirical_fallback` behavior for nonpositive scale rows, and vol63 uses the same prospective control rule. Negative/null volatility remains a hard error.

No rows are dropped, no epsilon is introduced, and no alpha/lambda/kappa grid, feature, primary reference, quantile, horizon, fold or score is changed. The plan gate now reports zero/negative/null scale support from the real Core V003 DB. This amendment must be committed before rerunning V007.
<!-- MARKET_DIST_V007_ZERO_VOL_AMENDMENT_V0011_END -->

<!-- MARKET_DISTRIBUTIONAL_V008_V001_START -->
## 2026-08-27 — Stop handcrafted scale tuning; test conditional information sufficiency

Decision: reject V007 without post-result tuning and preregister V008 Conditional Residual Quantiles.

Rationale: V007 lost to vol63 at all horizons, so another handcrafted volatility formula would be post-hoc specification search. V008 instead asks whether the existing causal endogenous Market State contains information about future standardized-return shape after a strong vol63 scale and recent train-only recalibration are already accounted for.

The full endogenous feature family is primary. Same-capacity scale-only and own-state variants are diagnostics only. If the primary fails, no diagnostic feature family is auto-promoted; a later experiment must preregister any narrower model. A broad V008 failure is interpreted as evidence that the information state is insufficient beyond calibrated volatility, not as permission to increase tree depth, add a neural network, or tune more windows.
<!-- MARKET_DISTRIBUTIONAL_V008_V001_END -->

<!-- MARKET_V008_SPLIT_FEASIBILITY_V0011 -->
### Market Distributional V008 v0011 — pre-performance split-feasibility amendment

The original V008 v001 benchmark aborted before any model fit or OOS performance metric because the earliest 30% outer fold could not simultaneously satisfy 126 recent calibration origin days, 126 minimum nested validation origin days, and 500 minimum nested training origin days after purging. No V008 performance was observed.

V008 v0011 preserves the frozen scientific question, features, H1/H3/H5/H10, five 30%-initial purged expanding outer folds, 126-day recent calibration window, 126-day minimum inner validation, HGB profile set, vol63_recent_calibrated primary reference, metrics, bootstrap and gates. The only scientific-control change is `minimum_inner_train_origin_days: 500 -> 378` (1.5 trading years) for nested profile selection. Final fold models remain fit on the full development block. The plan now performs a clock-only conservative split-feasibility audit before benchmarking.

## D027 — V008 rejects its contract, not all endogenous information

**Decision:** close V008 as four significant primary failures and reject its
recent-recalibrated full-endogenous candidate without tuning.

Evidence:

- full vs recent-calibrated vol63 block-10 intervals are below zero at
  H1/H3/H5/H10;
- full also loses to raw vol63 at every horizon;
- full loses to the own-state and scale-only HGB controls at every horizon;
- the shallow profile is selected in all 20 nested selections;
- the 126-origin-day recent recalibration harms raw vol63 at every horizon.

The valid claim is deliberately narrow: the current feature family,
standardized-return representation, HGB learner and calibration contract did
not add stable information beyond raw vol63. This result does not prove that
all endogenous market information is absent, and it does not by itself identify
whether information, representation, pooling or objective is the limiting
factor.

**Status:** V008 complete; no model promoted.

## D028 — One final raw own-state closure gate before external information

**Decision:** preregister V008.1 as a final developmental falsification of the
only remaining V008 ambiguity.

V008.1 freezes:

- H1 as the primary post-V008 hypothesis;
- H3/H5/H10 as diagnostics that cannot rescue H1;
- raw vol63 as the primary reference;
- the exact 14-feature V008 own-state family;
- the V008 shallow regularized HGB profile, with no selection;
- complete purged outer-train fitting;
- no recent quantile or probability recalibration;
- equal-origin-day pinball and 5/10/20-day moving-block bootstrap;
- a five-seed same-capacity H1 placebo preserving aligned volatility features
  while jointly deranging other own-state features within origin day;
- calibration, quantile breadth, fold stability and placebo gates in addition
  to the primary score interval.

Because H1 was selected after observing V008, even a full V008.1 pass on the
same historical sample is developmental only. Promotion requires a genuinely
untouched temporal holdout. Failure closes the current endogenous daily
price/volume engineering branch and triggers a separate decision about one
causally versioned external information block.

**Status:** completed; H1 passed every frozen developmental gate. The
supported increment is distribution shape/tails, not location or alpha.

## D029 -- Confirm V008.1 prospectively with one frozen fit and immutable records

**Decision:** preregister V009 as the only promotion path for the V008.1 H1
own-state result.

V009 freezes:

- one fit using usable H1 targets ending strictly before the prospective start;
- no refit, feature change, calibration or hyperparameter selection during the
  confirmatory window;
- the fixed 497-asset current-cohort snapshot from 2026-08-24;
- actual seal time no later than 16 hours after the state close;
- no retrospective prediction backfill and no skipped eligible origin;
- separate append-only prediction, outcome, score and evaluation records;
- the first 126 origins as descriptive only;
- the first 252 consecutive eligible resolved origins as the only formal gate;
- raw vol63 as reference, equal-origin-day pinball as primary score, 10-day
  moving-block uncertainty, at least 4/5 positive chronological blocks, at
  least three improved quantiles and calibration not worse.

Reason: V008.1 reused history already inspected by V008. Its H1 point delta
(`+0.004703 pp`, block-10 95% interval
`[+0.002923,+0.006582]`) is broad and placebo-resistant, but it cannot be
independent confirmation. A static fit over the prospective block matches the
historical fold test length better than introducing an unvalidated online
refit schedule.

Distributional Event Brain infrastructure may advance on the existing SEC
corpus while V009 accumulates, but its results cannot validate, rescue or
modify V009. Graph prediction remains blocked until direct event information
adds reproducible OOS value.

**Status:** V009 plan passed; additive migrations 021-022 are initialized and
the single pre-holdout fit is frozen. Daily source/Core refresh is operationally
gated to the first allowed origin, 2026-08-28; no V009 refit is permitted.

<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_START -->
## Decision — accumulate future information vintages without contaminating V009

**Decision:** permit a parallel append-only Expectation / Information Capture Foundation in a database separate from Market Core. It may collect live strict-PIT evidence while V009 accumulates, but none of its records are model-visible or allowed to validate, rescue, refit or modify V009. Historical expectation backfills must remain explicitly non-strict-PIT.

**Reason:** beliefs, revisions, guidance, scheduled uncertainty and actual-vs-expectation surprise are scientifically important but are difficult to reconstruct faithfully after the fact. Capturing them prospectively creates future research data without changing the frozen Market Brain experiment.
<!-- EXPECTATION_CAPTURE_FOUNDATION_V001_END -->

## D030 — Separate residual-future event research from first-shock attribution

**Pre-performance data-contract decision (2026-08-28):** build
`distributional_event_close_aligned_v001` from existing SEC V003.1 and Market
Core V003 without changing either source or V009. The origin is the first
exchange close strictly after validated evidence/state availability. Old
event-reaction labels are not interchangeable with this new target.

Keep public, retrieval, linking, version and prediction clocks distinct; first
public disclosure remains unknown unless supported by evidence. Later evidence
cannot retroactively enrich an earlier state. One row per asset-close and
scenario, with event/filing/content groups, prevents mechanical duplication of
the same outcome. Zero/one-hour/24-hour additional-delay scenarios are timing
sensitivities, not fitted latency or economic decay.

This narrows the estimand to remaining future distributions. It does not claim
immediate reaction, execution at the close, novelty, causation or strict PIT.
The full corpus audit and a separate frozen experimental protocol are required
before training. No performance, primary horizon or model is selected here.

**Status:** residual-future estimand retained; V001 materialized but its HTTP
clock implementation was rejected. D031 and V002 supersede that implementation.

## D031 — HTTP modification metadata cannot manufacture historical arrivals

**Data-contract correction, not performance tuning (2026-08-28):** preserve the
completed V001 dataset/reports but prohibit training on that contract. The SEC
ingestor stores HTTP Last-Modified in raw modified_at; this field is not proof
of a new economic publication or a historically observed byte revision.

V002 verifies the downloader/header/acceptance provenance, keeps modification
metadata separate, and requires every admitted information clock to belong to
the historical snapshot. Unknown/later availability is quarantined, not
silently repaired by moving an old event forward. Observed byte revisions remain
excluded without a valid separate version clock. Historical reconstruction
remains PIT=0 and first public disclosure remains unestablished.

No missing exact Market Core state may be rescued by selecting a later close.
Cross-accession ambiguity remains explicit quarantine with as-of diagnostics;
there are no automatic merges, deletions or upstream state rewrites.
The independent audit checks unexplained shifts, arrival concentration,
state-year coverage, bidirectional alignment and corporate-action selection.

**Status:** V002 infrastructure and small real regression verified; full V002
audit/review and an independent model preregistration still required.
Sources, old artifacts and the frozen V009 protocol are unchanged.
See [DISTRIBUTIONAL_EVENT_DATASET_V002.md](DISTRIBUTIONAL_EVENT_DATASET_V002.md).

## D033 — Accept V002 with frozen exclusions; do not repair coverage by retiming

**Post-materialization data decision (2026-08-29), before model performance:**
the complete V002 artifact passes current persisted/replay integrity over all
2,001 source states and has zero unexplained information-boundary shifts.

Primary-dataset disposition:

- retain 1,885 temporally eligible states;
- exclude 115 cross-accession cluster states rather than selecting a convenient
  subset of their evidence after seeing outcomes;
- retain the one AAPL same-file/multi-reference case in lineage but exclude it
  from immutable V002; it is a technical duplicate reference, not evidence of
  a second event or byte revision;
- do not create Market Core states for the 151 early unmatched origins merely
  to increase event sample size; the model-visible cohort begins only where the
  exact existing Core contract is available;
- do not reinterpret three delay scenarios as 4,086 independent observations.

This yields 1,734 selected event-state links in the zero-delay projection,
1,365 asset-close samples and 1,351 usable H1 outcomes. H10 loses 332/1,365
rows (24.3%) to corporate actions under that scenario, so long-horizon claims
require explicit selection sensitivity and cannot rescue a failed primary.

The five historical V008.1 H1 test windows are structurally feasible on V002.
After outcome and event/filing/content purges, their train/test row counts are:
`395/212`, `613/189`, `803/180`, `983/196`, `1179/172`.
This supports a small regularized developmental Event Head, not a high-capacity
model justified by architecture alone.

**Status:** V002 preparation/exclusion review complete. Training remains blocked
until a separate Distributional Event Brain protocol freezes the primary
horizon/scenario, historical Market Brain refits, low-capacity event/control
heads, placebo seeds, folds, weights, scores and dependent uncertainty.
V009 is neither a historical control nor affected by this decision.

## D032 — Reject partial Yahoo daily bars; allow only bounded regular-session close metadata

**Pre-first-seal data-contract correction (2026-08-29), not model tuning:**
the 2026-08-28 Yahoo daily chart response contained Open/High/Low/Volume for
all requested assets but null Close/Adj Close. No V009 prediction was sealed
and no model output or H1 outcome was evaluated.

Refresh V002 may fill only a missing daily Close from the same provider's
regularMarketPrice when all of the following hold: regularMarketTime is
between the exchange close and five minutes after it, the retrieval occurs
after that timestamp, the price is positive and lies inside the daily Low/High,
and exactly one origin row exists. postMarketPrice is forbidden. Adj Close is
not manufactured and remains audit-only. The original null daily Close,
fallback field, timestamp and value remain in raw provider-library lineage.

Migration 023 adds, rather than rewrites, a V002 quality view. Failed
retrievals remain persisted. The first quality-eligible observation receives
the existing explicitly non-PIT session-close reconstruction semantics;
actual observed_at remains preserved. Later eligible revisions retain their
retrieval availability. Historical selected-row counts through 2026-08-24 are
unchanged (1,240,503 under both V001 and V002), and the frozen V009 training
hash must still pass before any Core replacement.

The 2026-08-28 prediction deadline is not extended and that origin may not be
backfilled. V009's frozen cohort begins with its first sealed batch, which is
now pending no earlier than 2026-08-31. The fit, features, hyperparameters,
reference and promotion gate remain unchanged.

**Status:** implemented; migration 023 applied; 38 relevant tests pass; five
real assets completed with quality PASS and zero missing origins. The remaining
492-asset acquisition and full Core/hash audit are user-run computational
steps.

## D034 — Represent horizon as tau; default to sparse falsifiable materialization

**Pre-model data decision (2026-08-30):** Market Temporal V001 exposes the
integer exchange-session domain `tau=1..252`. The final representation is not a
fixed list of horizon heads and no output interpolation rule is authorized.

The materializer supports:

- the frozen 17-tau anchor/holdout set as the default;
- that set plus declared on-demand taus;
- a fully dense H1..H252 artifact.

The default remains sparse because 1,092,555 Core origins imply 18,573,435
rows at 17 taus but 275,323,860 rows at 252 taus. Nearby and overlapping labels
are strongly dependent, so dense expansion increases storage/compute by about
14.8x without multiplying independent evidence.

Falsifiability is preserved by keeping training anchors separate from
temporal-generalization holdouts H7/H17/H42/H90/H180. Holdouts may be
materialized, but cannot drive model selection. Extra taus must be declared;
post-outcome horizon selection is forbidden.

Before any training:

1. H1/H3/H5/H10 must reproduce Core target day, raw-close return,
   corporate-action overlap and status exactly;
2. H21/H63/H126/H252 selection must be reported by horizon, asset, sector and
   origin year;
3. raw-close long-horizon training remains blocked pending review;
4. a material overlap result requires a separately versioned causal
   total-return target, not provider Adjusted Close;
5. source/Core remain read-only and V009 remains isolated.

**Status:** implementation and real read-only plan complete; full materialized
selection evidence pending. No model was fit and no path claim follows.

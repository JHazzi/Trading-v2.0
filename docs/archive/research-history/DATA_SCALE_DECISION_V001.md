# Data Scale Decision v0.1 — why the current Event Brain is still a pilot

## What has been real so far

The current pipeline is not a toy:
- causal SEC metadata lineage;
- evidence/document availability;
- deterministic clustering;
- normalized event identities;
- event-state snapshots;
- 1/3/5/10-session reaction labels;
- purged walk-forward;
- capacity-controlled incremental Event-vs-Market comparison.

Those controls are exactly why weak/ambiguous results were visible instead of
being hidden by a random split.

## What is still statistically small

The first benchmark has roughly:
- 308 normalized events total;
- 230–282 unique usable events depending on horizon;
- about 100–250 training rows per outer fold;
- 135–167 pooled OOS rows;
- effective event history beginning in 2024.

The effective sample size is lower than the row count because:
- several events share the same market day;
- firms in the same sector are correlated;
- multiple states can belong to one event;
- overlapping multi-session returns are dependent.

Therefore "293 rows" is not equivalent to 293 independent experiments.

## Why training was fast

Runtime is not evidence of model seriousness.

A Random Forest with a few hundred rows and dozens of features can train very
quickly on a normal CPU. The important bottleneck is statistical information,
not FLOPs.

A huge neural model trained for hours on the same 293 rows would usually be
less trustworthy, not more.

## Scaling order

### Stage A — depth first

Keep the same 10-company cohort and expand SEC metadata backward.

Target:
- up to 250 filings per issuer target;
- 8–10 calendar years where SEC history allows it;
- preserve historical availability as research proxy (PIT=0);
- resolve issuer succession explicitly (XOM predecessor/successor).

Do not change Event Brain model architecture during this scale.

### Stage B — documents/events

Only after metadata coverage is audited:
- download historical filing documents causally;
- cluster new historical evidence;
- normalize without duplicating existing event states;
- rebuild labels;
- target an order of magnitude of 1,000+ distinct events.

A deduplication/lineage guard is required before mass normalization because a
deep rerun must not create duplicate "unchanged" states for events already
normalized in the pilot.

### Stage C — breadth

After history depth works:
- expand to ~30–50 assets;
- preserve multiple companies per sector;
- avoid one-company sector proxies;
- repeat the same evaluation contract.

### Stage D — stronger information

Only after the event signal survives depth + breadth:
- stronger market benchmarks / ETFs / macro;
- expectation and surprise data;
- official IR / earnings releases;
- news text and multi-source reliability;
- graph propagation.

## What would count as meaningful evidence

No single number is enough.

For a candidate event signal, require:
- positive incremental MAE vs same-capacity control;
- confidence interval preferably above zero;
- stability across random seeds;
- stability across temporal folds;
- non-pathological concentration by company or event type;
- survival after stronger Market-only baselines;
- survival when older market regimes are introduced.

The 10-session result is currently a hypothesis to test, not a discovered law.

# News Causal Time Contract — research draft

This document defines the time semantics required before linking news to market reactions.

## Two clocks, not one

An underlying event and a document describing it are distinct objects.

For a real-world event `e` and a document `d`:

- `event_occurrence_at(e)`: when the underlying event happened, if known.
- `published_at(d)`: timestamp asserted by the publisher.
- `first_seen_at(d)`: when the provider/system first observed the document.
- `available_at(d)`: earliest timestamp at which the actual predictor path could use the document.
- `reaction_start_at(e)`: outcome-side diagnostic estimated after the fact. Never a feature.
- `scheduled_for(e)`: future date/window known before occurrence, with precision and status.

There is no general equality between these timestamps.

### Live observable event example

A rocket explodes at 14:03 while the market is open. A live feed, eyewitness video or
exchange-relevant information reaches participants immediately. The first conventional
article is published at 14:05 and our crawler sees it at 14:06.

A price move beginning at 14:03 must not be attributed causally to the 14:05 article.
The article is later evidence about the same underlying event.

### Closed-market example

A material pharmaceutical result is released after the close. The next regular-session
bar opens dramatically higher. The correct initial causal object is the complete set of
new evidence available between the prior close and next open. A single article should
not receive exclusive attribution unless the evidence contract supports that claim.

## Epistemic status

Claims must be append-only observations with changing status, for example:

`rumor -> corroborated rumor -> company confirmation -> filing -> correction`

Never overwrite an earlier rumor with later truth. The model must be able to know what
was believed at each time.

Suggested claim/status vocabulary is descriptive, not a reliability score:

- observed_fact
- official_statement
- regulatory_filing
- scheduled_event
- forecast
- analyst_opinion
- media_report
- attributed_report
- unattributed_report
- rumor
- speculation
- correction
- denial

## Source properties

Do not hardcode one scalar "reliability". Separate future learnable/diagnostic dimensions:

- factual accuracy / later correction rate;
- timeliness / first-mover behavior;
- originality vs syndication;
- corroboration diversity;
- entity relevance;
- sensational framing;
- predictive usefulness;
- market impact;
- persistence.

Truth, predictive usefulness and market impact are different concepts.

## Duplicate coverage

Documents are evidence; stories/events are latent clusters.

Twenty copies of a Reuters story are not twenty independent shocks. They can still
represent propagation/attention. Preserve every document but map them to a story cluster.

Useful cluster-level observables later include:

- document count;
- distinct publisher count;
- distinct source-family count;
- first-seen time;
- propagation velocity;
- title/body semantic diversity;
- contradictory-claim count;
- novelty relative to recent clusters.

## Market outcome families

A news/event state may alter different parts of the future distribution. Do not evaluate
only signed terminal return.

Candidate descriptive outcomes include:

- abnormal return vs market/sector;
- opening gap;
- realized volatility;
- volume/liquidity shock;
- downside/upside tail;
- MFE / MAE;
- regime transition;
- persistence/half-life.

No-effect observations are essential controls.

## Historical reconstruction

Legacy news may be valuable for hypothesis generation and association studies, but
publisher timestamps do not prove historical `available_at`. Historical rows remain
`strict_pit=0` unless the acquisition/availability path is independently established.

No predictive promotion may rely on relabeling retrospective publication timestamps as
strict PIT.

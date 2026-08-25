# Deep Event Corpus V003 — architecture decision

## Source semantics

This corpus is SEC evidence, not general news.

A filing can contain:
- the filing itself;
- exhibits;
- official earnings releases;
- agreements;
- presentation material;
- XBRL-related files.

Even when an exhibit reads like a press release, its source semantics remain
`official_statement` because it was retrieved as evidence attached to an SEC
filing. Media/news evidence is a separate future source family.

## Why V003 exists

The pilot Event State v002 was built from roughly 2024–2026 evidence. Deep SEC
retrieval materially changed corpus depth and document coverage.

Appending old and new states under the same feature version would make
historical examples incomparable. V003 therefore rebuilds the full selected
event corpus under one feature version:

    sec_event_normalizer_v003_deep_rebuild
    event_state_v003_deep
    event_reaction_daily_v003_deep

The economic taxonomy itself is intentionally unchanged.

## Common cohort window

A target asset can have long standalone history while its cross-sectional peers
do not. Asset-local readiness is insufficient for a Market+Event dataset.

The start of V003 is therefore:

    max(21st quality-gated daily price day across cohort assets)

and its last supported day is:

    min(last quality-gated daily price day across cohort assets)

This protects the existing Market State requirement that all event examples can
be evaluated against a coherent multi-asset context.

AAPL documents older than the common window remain stored but are not clustered
into V003.

## Stable event identity

Economic identity remains:

    SEC accession + filing item/form identity

The deep rebuild reuses existing `normalized_event_identities`. It creates a new
normalization observation lineage and a new feature version, not a second
economic identity for the same event.

This lets the audit report:
- reused pilot event identities;
- genuinely new deep-history event identities.

## No new predictive assumptions

V003 still does not hardcode:
- bullish/bearish direction;
- event importance;
- source reliability;
- decay;
- expected return;
- trading action.

It also does not yet parse filing body text into claim-level economic surprises.

## Scientific objective

The first target is corpus scale, not a better score.

After labels are rebuilt we ask:
1. how many unique events exist?
2. how many are model-ready per horizon?
3. how many market regimes/years are represented?
4. does the same Event Brain v0.2 benchmark behave differently?
5. does the tentative 10-session incremental effect survive?

The Event Brain model is deliberately frozen until this dataset audit is
complete.

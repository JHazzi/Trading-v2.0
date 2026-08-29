# News / Narrative Evidence V001

## Purpose

Add ordinary company, market and macro news to the causal information architecture
without collapsing news into a bullish/bearish trading signal.

The architectural boundary is:

raw provider response
-> immutable source observation
-> news document observation
-> provider asset/topic annotations
-> later story clustering
-> later event/narrative state
-> only then an incremental predictive experiment

A document is evidence, not a shock.

## Sentiment policy

Provider sentiment and relevance scores are retained exactly as provider annotations.
They are not treated as truth, labels, return direction, source reliability or event
impact.

The future Narrative State may study, under a separate version:
- attention / coverage volume;
- source diversity;
- novelty;
- repetition / propagation velocity;
- disagreement and contradiction;
- uncertainty / modal language;
- provider and learned semantic embeddings;
- company / sector / market scope;
- interaction with expectations and surprise.

No one-dimensional `sentiment > 0 => bullish` rule is permitted.

## First source

Alpha Vantage NEWS_SENTIMENT is used as a low-friction first adapter because it can
return up to 1000 market-news items in a single request and includes ticker/topic
annotations. Initial capture is market-wide rather than one request per ticker.

Do not enable it while the V0013 rolling quota has zero remaining requests.

## Additional sources

GDELT is a candidate for broad global narrative/attention coverage. In 2026 its
legacy search infrastructure is undergoing migration toward GDELT 5, so it should be
treated as a second provider after the primary contract is validated rather than a
single point of dependency.

Direct publisher feeds and social sources require provider-specific licensing,
timestamp, deletion/edit, bot/spam and identity contracts before model visibility.

## Historical legacy news

Any old news corpus whose true first-seen/available timestamp cannot be proven may be
imported only as `strict_pit=0` research evidence. It cannot be silently upgraded to
strict PIT.

## V009

No V009 code, config, training artifact, Core state or prospective prediction can
reference this information class.

## Quota integration

The capture pipeline reads and updates the V0013 rolling Alpha Vantage request ledger. A failed NEWS_SENTIMENT request also consumes one ledger attempt. Capture is blocked automatically when remaining requests are zero.

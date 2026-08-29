# News/Event Lineage Review V001

Read-only gate after Information Archaeology V001.

The archaeology audit established that `market_data_v2.db` contains the full legacy
news corpus and substantial Event/Graph infrastructure, but `news_documents` itself
does not expose `available_at`.

This review therefore asks:

1. Can `news_documents` be joined to `raw_source_documents` or another upstream table
   carrying an availability/retrieval clock?
2. What fraction of news documents is covered by that lineage?
3. Does the parent timestamp occur before/after publisher time in plausible ways?
4. Which membership tables actually connect documents, stories/clusters and events?
5. How were `event_reaction_outcomes` and `normalized_event_reaction_labels` structured?
6. Which repository files write or reference these tables?

A joinable `available_at` is only a lineage candidate. Historical strict PIT is not
granted automatically. Acquisition semantics must still be verified.

No database writes, provider calls, feature creation or model training are performed.

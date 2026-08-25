# Event ↔ Market Mapping Contract

The project does not train `article -> return`.

It builds a causal map through five distinct layers:

1. Temporal map
   - `occurred_at`: when the underlying event happened, if known.
   - `scheduled_for`: when a known future event is expected.
   - evidence `published_at` / source observation time.
   - `available_at`: when the system could actually use the evidence.
   - It is valid for `occurred_at < available_at`; that lag is information.

2. Evidence map
   - Repeated/syndicated documents are grouped in run-scoped clusters.
   - A cluster is not automatically an economic event.
   - One cluster may produce multiple events; one event may collect evidence
     from multiple clusters.

3. Epistemic/semantic map
   Evidence is classified by communicative form:
   observed_fact, official_statement, reported_fact, opinion, forecast,
   rumor, speculation, correction, retraction, mixed, unknown.

   This does NOT say whether the claim is economically important, reliable,
   bullish or bearish. Those quantities are learned later.

4. Scope/entity map
   Events can directly link to companies, counterparties, sectors, industries,
   regulators, markets, etc.

   Direct event links and indirect graph propagation are separate:
   - direct evidence -> normalized_event_entity_links / asset_links
   - related companies / supply chain / competitors -> graph features later

5. Market-reaction map
   Later, every causal event state will be paired with realized outcomes:
   - intraday where coverage exists
   - close -> next open
   - next session
   - 1 / 3 / 5 / 10 sessions
   - asset absolute return
   - market-relative return
   - sector-relative return
   - MFE / MAE / realized volatility

The Event Brain will learn incremental distributional effects over the Market Brain.

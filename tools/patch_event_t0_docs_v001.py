from __future__ import annotations

import argparse
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]

PATCHES = {
    "ARCHITECTURE_EVENT_LAYER.md": (
        "## 3. Temporal lifecycle",
        """
<!-- EVENT_T0_V001_START -->
### Source authority is not first disclosure

For an economic event, the authoritative source and the first public source
may be different.

Define conceptually:

```text
first_public_at(event)
    = earliest legitimate public availability among evidence items
      that existed at that moment
```

Do **not** assume:

```text
SEC accepted_at == first_public_at == event_time
```

Examples include earnings releases published through Investor Relations or
a press-release wire before the corresponding 8-K, or a reported/rumored
transaction that precedes official confirmation.

The Event State must evolve through time. Later SEC evidence can improve
confirmation, detail and provenance without retroactively moving information
into an earlier state.

For intraday research, a model may react only to the evidence that was
actually/publicly available by the prediction timestamp.
<!-- EVENT_T0_V001_END -->

""",
    ),
    "docs/DATA_CONTRACTS.md": (
        "## 2. Strict PIT vs historical research reconstruction",
        """
<!-- EVENT_T0_V001_START -->
## First-public disclosure contract

`event_time`, `first_public_at`, `published_at`, `accepted_at`,
`observed_at` and `available_at` are different concepts.

For a normalized event:

- `first_public_at` is the earliest legitimate public evidence time known
  under the evidence history;
- SEC `accepted_at` is the SEC filing acceptance timestamp and may be equal
  to, earlier than, or later than another public disclosure channel;
- `available_at` is the feature gate for a specific evidence/state row;
- later confirmation must not be back-propagated into an earlier model state.

The future multi-source Event Layer should reconstruct the evidence sequence,
not choose one source globally as the universal `t0`.

SEC is an authoritative anchor source; it is not assumed to be the fastest
source for every event.
<!-- EVENT_T0_V001_END -->

""",
    ),
    "docs/RESEARCH_DECISIONS.md": (
        "## D019 — Documentation has canonical vs historical layers",
        """
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

""",
    ),
    "docs/ROADMAP.md": (
        "## Phase 2 — Market Brain Daily V003",
        """
<!-- EVENT_T0_V001_START -->
### Temporal integration with future Event Brain

Market V003 states are defined at exchange session close for all eligible
asset-days.

When Event Brain is reintroduced, an event at time `t` may only use the latest
Market Brain state/prediction with:

```text
market_state_time <= event_state_time
```

The event timestamp used for information availability is not automatically
the SEC acceptance time. Future multi-source event work will use the earliest
valid public evidence while preserving later confirmations as later evidence.
<!-- EVENT_T0_V001_END -->

""",
    ),
}


def apply_patch(path: Path, anchor: str, text: str, apply: bool) -> str:
    content = path.read_text(encoding="utf-8")
    if "<!-- EVENT_T0_V001_START -->" in content:
        return "already_applied"
    pos = content.find(anchor)
    if pos < 0:
        raise RuntimeError(f"Anchor not found in {path}: {anchor}")
    if apply:
        content = content[:pos] + text + content[pos:]
        path.write_text(content, encoding="utf-8")
    return "would_apply" if not apply else "applied"


def main() -> None:
    p = argparse.ArgumentParser()
    group = p.add_mutually_exclusive_group(required=True)
    group.add_argument("--check", action="store_true")
    group.add_argument("--apply", action="store_true")
    args = p.parse_args()

    results = {}
    for rel, (anchor, text) in PATCHES.items():
        path = ROOT / rel
        if not path.is_file():
            raise FileNotFoundError(path)
        results[rel] = apply_patch(path, anchor, text, args.apply)

    for rel, result in results.items():
        print(f"{result:15s} {rel}")


if __name__ == "__main__":
    main()

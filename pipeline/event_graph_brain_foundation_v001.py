from __future__ import annotations

import argparse
import json
from pathlib import Path

from database.apply_migration_020 import apply as apply_migration
from knowledge.entities.seed_asset_entity_proxies_v001 import (
    seed_asset_entity_proxies,
)
from ingestion.events.direct_event_entity_bridge_v001 import (
    bridge_direct_events,
)
from evaluation.events.event_graph_foundation_audit_v001 import audit


ROOT = Path(__file__).resolve().parents[1]
DEFAULT_CONFIG = (
    ROOT / "config" / "event_graph_brain_foundation_v001.json"
)


def load_config(path: Path) -> dict:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    if cfg["version"] != "event_graph_brain_foundation_v002":
        raise ValueError("unexpected foundation version")
    if cfg["graph_contract"]["foundation_max_hops"] != 1:
        raise ValueError("foundation graph must remain 1-hop")
    if cfg["graph_contract"]["relation_sign_hardcoded"]:
        raise ValueError("relation sign must not be hardcoded")
    if cfg["market_prior"]["do_not_tune_in_this_stage"] is not True:
        raise ValueError("V004 prior must remain frozen")
    return cfg


def main():
    p = argparse.ArgumentParser()
    p.add_argument(
        "--stage",
        required=True,
        choices=(
            "migrate",
            "seed-asset-entities",
            "bridge-direct-events",
            "audit",
        ),
    )
    p.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    p.add_argument("--as-of")
    a = p.parse_args()

    cfg = load_config(a.config)
    db = ROOT / cfg["database"]
    if a.stage == "migrate":
        result = {
            "status": "PASS",
            "migration": apply_migration(db),
        }
    elif a.stage == "seed-asset-entities":
        f = cfg["entity_contract"]["seed_asset_filter"]
        result = seed_asset_entity_proxies(
            db,
            asset_type=f["asset_type"],
            active=int(f["active"]),
        )
    elif a.stage == "bridge-direct-events":
        ev = cfg["existing_event_contract"]
        result = bridge_direct_events(
            db,
            event_feature_version=ev["event_feature_version"],
            as_of=a.as_of,
        )
    else:
        result = audit(db)

    print(json.dumps(result, indent=2))


if __name__ == "__main__":
    main()

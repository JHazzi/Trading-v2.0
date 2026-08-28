from __future__ import annotations

import json
from pathlib import Path
from typing import Any

REQUIRED_PROVIDER_FIELDS = {
    "provider_id",
    "role",
    "economic_latents",
    "historical_pit_quality",
    "prospective_strict_pit",
    "cost_class",
    "automation",
    "primary_risk",
    "recommended_use",
    "evidence_urls",
}


def load_registry(path: Path) -> dict[str, Any]:
    data = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(data.get("providers"), list) or not data["providers"]:
        raise ValueError("provider registry must contain providers")
    ids: set[str] = set()
    for provider in data["providers"]:
        missing = REQUIRED_PROVIDER_FIELDS - set(provider)
        if missing:
            raise ValueError(f"provider missing fields: {sorted(missing)}")
        pid = str(provider["provider_id"])
        if pid in ids:
            raise ValueError(f"duplicate provider_id: {pid}")
        ids.add(pid)
        if not provider["evidence_urls"]:
            raise ValueError(f"provider {pid} has no evidence_urls")
    sequence = data.get("recommended_sequence") or []
    unknown = [x for x in sequence if x not in ids]
    if unknown:
        raise ValueError(f"recommended_sequence has unknown providers: {unknown}")
    return data


def audit_registry(path: Path) -> dict[str, Any]:
    data = load_registry(path)
    providers = data["providers"]
    return {
        "status": "PASS",
        "registry_version": data.get("registry_version"),
        "provider_count": len(providers),
        "prospective_strict_pit_candidates": [p["provider_id"] for p in providers if p["prospective_strict_pit"]],
        "historical_pit_high_quality": [
            p["provider_id"] for p in providers
            if str(p["historical_pit_quality"]).startswith("strong")
        ],
        "first_capture_recommendation": (data.get("recommended_sequence") or [None])[0],
        "model_visibility": "BLOCKED",
        "interpretation": "Provider audit ranks information acquisition, not predictive performance. No provider is promoted to model input by this audit."
    }

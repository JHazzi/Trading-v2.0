from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime


@dataclass(frozen=True)
class RelationCandidate:
    source_entity_id: int
    target_entity_id: int
    relation_type: str
    relation_layer: str
    evidence_available_at: str
    evidence_type: str
    source_ref: str
    extractor_version: str
    extractor_confidence: float | None = None


def validate_candidate(
    candidate: RelationCandidate,
    *,
    allowed_relation_types: set[str],
) -> None:
    if candidate.source_entity_id == candidate.target_entity_id:
        raise ValueError("self relation is not allowed")
    if candidate.relation_layer != "structural":
        raise ValueError(
            "foundation candidate contract accepts structural relations only"
        )
    if candidate.relation_type not in allowed_relation_types:
        raise ValueError(
            f"unknown/not-approved relation_type={candidate.relation_type}"
        )
    if not candidate.source_ref.strip():
        raise ValueError("source_ref is required")
    if not candidate.evidence_type.strip():
        raise ValueError("evidence_type is required")
    if not candidate.extractor_version.strip():
        raise ValueError("extractor_version is required")
    # ISO parsing is validation only; timezone policy is enforced by source
    # ingestors before candidate promotion.
    datetime.fromisoformat(
        candidate.evidence_available_at.replace("Z", "+00:00")
    )
    if candidate.extractor_confidence is not None and not (
        0.0 <= candidate.extractor_confidence <= 1.0
    ):
        raise ValueError("extractor_confidence outside [0,1]")

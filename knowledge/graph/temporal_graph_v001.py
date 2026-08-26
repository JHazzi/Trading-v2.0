from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join(str(x) for x in parts).encode("utf-8")
    return prefix + "_" + hashlib.sha256(raw).hexdigest()


def promote_structural_relation(
    conn: sqlite3.Connection,
    *,
    source_entity_id: int,
    target_entity_id: int,
    relation_type: str,
    evidence_available_at: str,
    evidence_type: str,
    source_ref: str,
    assertion_version: str,
    availability_basis: str,
    availability_is_point_in_time: int,
    observed_at: str | None = None,
    valid_from: str | None = None,
    valid_to: str | None = None,
    evidence_sha256: str | None = None,
    metadata: dict | None = None,
) -> tuple[str, str]:
    """
    Promote a validated factual relation plus its first temporal observation.

    This function accepts no market sign, impact or predictive weight.
    """
    if source_entity_id == target_entity_id:
        raise ValueError("self relation is not allowed")
    if availability_is_point_in_time not in (0, 1):
        raise ValueError("availability_is_point_in_time must be 0/1")

    known = conn.execute(
        "SELECT 1 FROM relation_types WHERE relation_type=?",
        (relation_type,),
    ).fetchone()
    if known is None:
        raise ValueError(f"unknown relation_type={relation_type}")

    assertion_id = stable_id(
        "tra",
        source_entity_id,
        target_entity_id,
        relation_type,
        "structural",
        valid_from or "",
        assertion_version,
    )
    conn.execute(
        """
        INSERT OR IGNORE INTO temporal_relation_assertions_v001(
            relation_assertion_id,source_entity_id,target_entity_id,
            relation_type,relation_layer,valid_from,valid_to,
            assertion_version,metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?)
        """,
        (
            assertion_id,
            source_entity_id,
            target_entity_id,
            relation_type,
            "structural",
            valid_from,
            valid_to,
            assertion_version,
            json.dumps(metadata or {}, sort_keys=True),
        ),
    )

    existing_observation = conn.execute(
        """
        SELECT relation_observation_id
        FROM temporal_relation_observations_v001
        WHERE relation_assertion_id=?
          AND observation_action='asserted'
          AND evidence_available_at=?
          AND evidence_type=?
          AND source_ref=?
          AND COALESCE(evidence_sha256,'')=COALESCE(?,'')
        ORDER BY observation_sequence
        LIMIT 1
        """,
        (
            assertion_id,
            evidence_available_at,
            evidence_type,
            source_ref,
            evidence_sha256,
        ),
    ).fetchone()
    if existing_observation is not None:
        return assertion_id, str(existing_observation[0])

    last = conn.execute(
        """
        SELECT COALESCE(MAX(observation_sequence),0)
        FROM temporal_relation_observations_v001
        WHERE relation_assertion_id=?
        """,
        (assertion_id,),
    ).fetchone()[0]
    seq = int(last) + 1
    observed = observed_at or utc_now()
    observation_id = stable_id(
        "tro",
        assertion_id,
        seq,
        evidence_available_at,
        source_ref,
        evidence_sha256 or "",
    )
    conn.execute(
        """
        INSERT INTO temporal_relation_observations_v001(
            relation_observation_id,relation_assertion_id,
            observation_action,evidence_available_at,observed_at,
            availability_basis,availability_is_point_in_time,
            evidence_type,source_ref,evidence_sha256,observation_sequence,
            metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            observation_id,
            assertion_id,
            "asserted",
            evidence_available_at,
            observed,
            availability_basis,
            availability_is_point_in_time,
            evidence_type,
            source_ref,
            evidence_sha256,
            seq,
            json.dumps(metadata or {}, sort_keys=True),
        ),
    )
    return assertion_id, observation_id


def add_relation_observation(
    conn: sqlite3.Connection,
    *,
    relation_assertion_id: str,
    observation_action: str,
    evidence_available_at: str,
    evidence_type: str,
    source_ref: str,
    availability_basis: str,
    availability_is_point_in_time: int,
    observed_at: str | None = None,
    evidence_sha256: str | None = None,
    metadata: dict | None = None,
) -> str:
    if observation_action not in {
        "asserted", "confirmed", "corrected", "retracted"
    }:
        raise ValueError("invalid observation_action")
    existing = conn.execute(
        """
        SELECT relation_observation_id
        FROM temporal_relation_observations_v001
        WHERE relation_assertion_id=?
          AND observation_action=?
          AND evidence_available_at=?
          AND evidence_type=?
          AND source_ref=?
          AND COALESCE(evidence_sha256,'')=COALESCE(?,'')
        ORDER BY observation_sequence
        LIMIT 1
        """,
        (
            relation_assertion_id,
            observation_action,
            evidence_available_at,
            evidence_type,
            source_ref,
            evidence_sha256,
        ),
    ).fetchone()
    if existing is not None:
        return str(existing[0])

    last = conn.execute(
        """
        SELECT COALESCE(MAX(observation_sequence),0)
        FROM temporal_relation_observations_v001
        WHERE relation_assertion_id=?
        """,
        (relation_assertion_id,),
    ).fetchone()[0]
    seq = int(last) + 1
    obs_id = stable_id(
        "tro",
        relation_assertion_id,
        seq,
        evidence_available_at,
        source_ref,
        evidence_sha256 or "",
    )
    conn.execute(
        """
        INSERT INTO temporal_relation_observations_v001(
            relation_observation_id,relation_assertion_id,
            observation_action,evidence_available_at,observed_at,
            availability_basis,availability_is_point_in_time,
            evidence_type,source_ref,evidence_sha256,observation_sequence,
            metadata_json
        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?)
        """,
        (
            obs_id,
            relation_assertion_id,
            observation_action,
            evidence_available_at,
            observed_at or utc_now(),
            availability_basis,
            availability_is_point_in_time,
            evidence_type,
            source_ref,
            evidence_sha256,
            seq,
            json.dumps(metadata or {}, sort_keys=True),
        ),
    )
    return obs_id


def structural_relations_asof(
    conn: sqlite3.Connection,
    as_of: str,
) -> list[dict]:
    """
    Reconstruct G_t from observations available through `as_of`.

    A relation becomes visible only after an asserted/confirmed/corrected
    observation is available, and disappears after a retraction available by
    `as_of`. `valid_from/valid_to` are additional real-world validity bounds;
    availability remains the anti-leakage gate.
    """
    rows = conn.execute(
        """
        WITH visible_obs AS (
            SELECT
                o.*,
                ROW_NUMBER() OVER (
                    PARTITION BY o.relation_assertion_id
                    ORDER BY
                        julianday(o.evidence_available_at) DESC,
                        o.observation_sequence DESC,
                        o.relation_observation_id DESC
                ) AS rn
            FROM temporal_relation_observations_v001 o
            WHERE julianday(o.evidence_available_at) <= julianday(?)
        )
        SELECT
            a.relation_assertion_id,
            a.source_entity_id,
            a.target_entity_id,
            a.relation_type,
            a.relation_layer,
            a.valid_from,
            a.valid_to,
            a.assertion_version,
            o.observation_action,
            o.evidence_available_at,
            o.availability_is_point_in_time,
            o.source_ref
        FROM temporal_relation_assertions_v001 a
        JOIN visible_obs o
          ON o.relation_assertion_id=a.relation_assertion_id
         AND o.rn=1
        WHERE a.relation_layer='structural'
          AND o.observation_action <> 'retracted'
          AND (
              a.valid_from IS NULL OR
              julianday(a.valid_from) <= julianday(?)
          )
          AND (
              a.valid_to IS NULL OR
              julianday(?) < julianday(a.valid_to)
          )
        ORDER BY
            a.source_entity_id,
            a.target_entity_id,
            a.relation_type,
            a.relation_assertion_id
        """,
        (as_of, as_of, as_of),
    ).fetchall()

    names = [
        "relation_assertion_id",
        "source_entity_id",
        "target_entity_id",
        "relation_type",
        "relation_layer",
        "valid_from",
        "valid_to",
        "assertion_version",
        "observation_action",
        "evidence_available_at",
        "availability_is_point_in_time",
        "source_ref",
    ]
    return [dict(zip(names, row)) for row in rows]

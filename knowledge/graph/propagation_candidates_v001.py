from __future__ import annotations

import hashlib
import json
import sqlite3
from collections import defaultdict
from datetime import datetime, timezone
from pathlib import Path

from knowledge.graph.temporal_graph_v001 import structural_relations_asof


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join(str(x) for x in parts).encode("utf-8")
    return prefix + "_" + hashlib.sha256(raw).hexdigest()


def _entity_assets(conn: sqlite3.Connection) -> dict[int, list[int]]:
    rows = conn.execute(
        """
        SELECT ae.entity_id,a.asset_id
        FROM asset_entities ae
        JOIN assets a ON a.asset_id=ae.asset_id
        WHERE a.asset_type='equity' AND a.active=1
        ORDER BY ae.entity_id,a.asset_id
        """
    ).fetchall()
    out: dict[int, list[int]] = defaultdict(list)
    for entity_id, asset_id in rows:
        out[int(entity_id)].append(int(asset_id))
    return dict(out)


def generate_for_event(
    conn: sqlite3.Connection,
    *,
    event_id: str,
    as_of: str,
    max_hops: int = 1,
    allowed_relation_types: set[str] | None = None,
) -> list[dict]:
    if max_hops != 1:
        raise ValueError(
            "foundation primary supports exactly one graph hop"
        )

    links = conn.execute(
        """
        SELECT entity_id,asset_id,first_available_at,link_role
        FROM event_entity_links_v001
        WHERE event_id=?
          AND julianday(first_available_at) <= julianday(?)
        ORDER BY julianday(first_available_at),entity_id,asset_id
        """,
        (event_id, as_of),
    ).fetchall()
    if not links:
        return []

    rels = structural_relations_asof(conn, as_of)
    if allowed_relation_types is not None:
        rels = [
            x for x in rels
            if x["relation_type"] in allowed_relation_types
        ]

    adjacency: dict[int, list[tuple[int, dict, str]]] = defaultdict(list)
    for rel in rels:
        s = int(rel["source_entity_id"])
        t = int(rel["target_entity_id"])
        adjacency[s].append((t, rel, "outgoing"))
        adjacency[t].append((s, rel, "incoming"))

    entity_assets = _entity_assets(conn)
    result: list[dict] = []
    seen: set[tuple] = set()

    for source_entity_id, direct_asset_id, event_available_at, role in links:
        source_entity_id = int(source_entity_id)

        # 0-hop direct subject. This is not graph propagation.
        direct_targets = (
            [int(direct_asset_id)]
            if direct_asset_id is not None
            else entity_assets.get(source_entity_id, [])
        )
        if direct_targets:
            for asset_id in direct_targets:
                key = (
                    event_id, source_entity_id, source_entity_id,
                    asset_id, 0, "direct"
                )
                if key in seen:
                    continue
                seen.add(key)
                result.append({
                    "event_id": event_id,
                    "event_available_at": str(event_available_at),
                    "source_entity_id": source_entity_id,
                    "target_entity_id": source_entity_id,
                    "target_asset_id": asset_id,
                    "hop_count": 0,
                    "path_entity_ids": [source_entity_id],
                    "path_relation_assertion_ids": [],
                    "path_edge_orientations": [],
                    "exposure_kind": "direct",
                    "metadata": {"direct_link_role": str(role)},
                })

        # 1-hop structural exposure candidates. Traversal is symmetric for
        # discovery only; the original edge orientation is recorded.
        for target_entity_id, rel, orientation in adjacency.get(
            source_entity_id, []
        ):
            for asset_id in entity_assets.get(int(target_entity_id), []):
                key = (
                    event_id, source_entity_id, int(target_entity_id),
                    asset_id, 1, "graph"
                )
                if key in seen:
                    continue
                seen.add(key)
                result.append({
                    "event_id": event_id,
                    "event_available_at": str(event_available_at),
                    "source_entity_id": source_entity_id,
                    "target_entity_id": int(target_entity_id),
                    "target_asset_id": asset_id,
                    "hop_count": 1,
                    "path_entity_ids": [
                        source_entity_id, int(target_entity_id)
                    ],
                    "path_relation_assertion_ids": [
                        str(rel["relation_assertion_id"])
                    ],
                    "path_edge_orientations": [orientation],
                    "exposure_kind": "graph",
                    "metadata": {
                        "relation_type": rel["relation_type"],
                        "relation_evidence_available_at": (
                            rel["evidence_available_at"]
                        ),
                        "market_direction_assigned": False,
                        "market_weight_assigned": False,
                    },
                })

    return result


def persist_run(
    db: Path,
    *,
    event_ids: list[str],
    as_of: str,
    propagation_version: str,
    allowed_relation_types: set[str],
    max_hops: int = 1,
) -> dict:
    selection = {
        "event_ids": sorted(set(event_ids)),
        "as_of": as_of,
        "max_hops": max_hops,
        "relation_layer": "structural",
        "relation_types": sorted(allowed_relation_types),
    }
    cfg_hash = hashlib.sha256(
        json.dumps(selection, sort_keys=True).encode("utf-8")
    ).hexdigest()
    run_id = stable_id(
        "gprun",
        propagation_version,
        cfg_hash,
    )
    result = {
        "status": "PASS",
        "propagation_run_id": run_id,
        "events_requested": len(selection["event_ids"]),
        "events_with_candidates": 0,
        "direct_candidates": 0,
        "graph_candidates": 0,
        "failures": [],
    }

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        conn.execute(
            """
            INSERT OR REPLACE INTO graph_propagation_runs_v001(
                propagation_run_id,propagation_version,started_at,finished_at,
                status,as_of,max_hops,relation_layers_json,
                relation_types_json,event_selection_json,
                configuration_sha256,direct_candidates_written,
                graph_candidates_written,error_json
            ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?,NULL)
            """,
            (
                run_id,
                propagation_version,
                utc_now(),
                None,
                "running",
                as_of,
                max_hops,
                json.dumps(["structural"]),
                json.dumps(sorted(allowed_relation_types)),
                json.dumps(selection, sort_keys=True),
                cfg_hash,
                0,
                0,
            ),
        )
        conn.commit()

        try:
            for event_id in selection["event_ids"]:
                candidates = generate_for_event(
                    conn,
                    event_id=event_id,
                    as_of=as_of,
                    max_hops=max_hops,
                    allowed_relation_types=allowed_relation_types,
                )
                if candidates:
                    result["events_with_candidates"] += 1
                for c in candidates:
                    cid = stable_id(
                        "gpc",
                        run_id,
                        c["event_id"],
                        c["source_entity_id"],
                        c["target_entity_id"],
                        c["target_asset_id"],
                        c["hop_count"],
                        c["exposure_kind"],
                    )
                    conn.execute(
                        """
                        INSERT OR IGNORE INTO
                        graph_propagation_candidates_v001(
                            propagation_candidate_id,propagation_run_id,
                            event_id,event_available_at,source_entity_id,
                            target_entity_id,target_asset_id,hop_count,
                            path_entity_ids_json,
                            path_relation_assertion_ids_json,
                            path_edge_orientations_json,exposure_kind,
                            metadata_json
                        ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                        """,
                        (
                            cid,
                            run_id,
                            c["event_id"],
                            c["event_available_at"],
                            c["source_entity_id"],
                            c["target_entity_id"],
                            c["target_asset_id"],
                            c["hop_count"],
                            json.dumps(c["path_entity_ids"]),
                            json.dumps(
                                c["path_relation_assertion_ids"]
                            ),
                            json.dumps(c["path_edge_orientations"]),
                            c["exposure_kind"],
                            json.dumps(c["metadata"], sort_keys=True),
                        ),
                    )
                    if c["exposure_kind"] == "direct":
                        result["direct_candidates"] += 1
                    else:
                        result["graph_candidates"] += 1

            conn.execute(
                """
                UPDATE graph_propagation_runs_v001
                SET finished_at=?,status='completed',
                    direct_candidates_written=?,
                    graph_candidates_written=?
                WHERE propagation_run_id=?
                """,
                (
                    utc_now(),
                    result["direct_candidates"],
                    result["graph_candidates"],
                    run_id,
                ),
            )
            conn.commit()
        except Exception as exc:
            conn.rollback()
            result["status"] = "FAIL"
            result["failures"].append(
                f"{type(exc).__name__}: {exc}"
            )
            conn.execute(
                """
                UPDATE graph_propagation_runs_v001
                SET finished_at=?,status='failed',error_json=?
                WHERE propagation_run_id=?
                """,
                (
                    utc_now(),
                    json.dumps(result["failures"]),
                    run_id,
                ),
            )
            conn.commit()
            raise

    return result

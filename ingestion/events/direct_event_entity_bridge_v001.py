from __future__ import annotations

import hashlib
import json
import sqlite3
from datetime import datetime, timezone
from pathlib import Path


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(prefix: str, *parts: object) -> str:
    raw = "\0".join(str(x) for x in parts).encode("utf-8")
    return prefix + "_" + hashlib.sha256(raw).hexdigest()


def _columns(conn: sqlite3.Connection, table: str) -> set[str]:
    return {
        str(row[1])
        for row in conn.execute(f"PRAGMA table_info({table})")
    }


def bridge_direct_events(
    db: Path,
    *,
    event_feature_version: str = "event_state_v002",
    link_version: str = "direct_event_asset_entity_bridge_v001",
    as_of: str | None = None,
) -> dict:
    """
    Promote the already validated direct event->asset association into the
    entity layer.

    `state_time` is used as the earliest defensible model-usable timestamp for
    this bridge. This does not infer new companies, counterparties or impacts.
    """
    run_id = stable_id(
        "eelrun",
        link_version,
        event_feature_version,
        as_of or "all",
    )
    started = utc_now()
    selection = {
        "event_feature_version": event_feature_version,
        "as_of": as_of,
        "source_table": "normalized_event_state_snapshots",
    }
    cfg_hash = hashlib.sha256(
        json.dumps(selection, sort_keys=True).encode("utf-8")
    ).hexdigest()

    result = {
        "status": "PASS",
        "link_run_id": run_id,
        "events": 0,
        "event_asset_pairs": 0,
        "links_written": 0,
        "missing_asset_entity_pairs": 0,
        "pit_links": 0,
        "non_pit_links": 0,
        "failures": [],
    }

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        cols = _columns(conn, "normalized_event_state_snapshots")
        required = {
            "event_state_id",
            "event_id",
            "asset_id",
            "state_time",
            "feature_version",
        }
        missing = sorted(required - cols)
        if missing:
            raise RuntimeError(
                f"normalized_event_state_snapshots missing {missing}"
            )

        pit_expr = (
            "point_in_time_evidence_fraction"
            if "point_in_time_evidence_fraction" in cols
            else "0.0"
        )
        asof_sql = ""
        params: list[object] = [event_feature_version]
        if as_of is not None:
            asof_sql = " AND julianday(state_time) <= julianday(?) "
            params.append(as_of)

        rows = conn.execute(
            f"""
            WITH ranked AS (
                SELECT
                    event_id,
                    asset_id,
                    state_time,
                    {pit_expr} AS pit_fraction,
                    ROW_NUMBER() OVER (
                        PARTITION BY event_id,asset_id
                        ORDER BY julianday(state_time), event_state_id
                    ) AS rn
                FROM normalized_event_state_snapshots
                WHERE feature_version=?
                {asof_sql}
            )
            SELECT
                r.event_id,
                r.asset_id,
                r.state_time,
                r.pit_fraction,
                ae.entity_id
            FROM ranked r
            LEFT JOIN asset_entities ae ON ae.asset_id=r.asset_id
            WHERE r.rn=1
            ORDER BY julianday(r.state_time),r.event_id,r.asset_id
            """,
            params,
        ).fetchall()

        existing_run = conn.execute(
            """
            SELECT status,configuration_sha256
            FROM event_entity_link_runs_v001
            WHERE link_run_id=?
            """,
            (run_id,),
        ).fetchone()
        if existing_run is not None:
            if str(existing_run[1]) != cfg_hash:
                raise RuntimeError(
                    "existing direct-event bridge run has different config hash"
                )
            # Never REPLACE a run row: REPLACE is DELETE+INSERT in SQLite and
            # could detach provenance through ON DELETE behavior.
            conn.execute(
                """
                UPDATE event_entity_link_runs_v001
                SET started_at=?,finished_at=NULL,status='running',
                    links_written=0,error_json=NULL
                WHERE link_run_id=?
                """,
                (started, run_id),
            )
        else:
            conn.execute(
                """
                INSERT INTO event_entity_link_runs_v001(
                    link_run_id,link_version,started_at,finished_at,status,
                    as_of,selection_json,configuration_sha256,links_written,
                    error_json
                ) VALUES (?,?,?,?,?,?,?,?,?,NULL)
                """,
                (
                    run_id,
                    link_version,
                    started,
                    None,
                    "running",
                    as_of,
                    json.dumps(selection, sort_keys=True),
                    cfg_hash,
                    0,
                ),
            )
        conn.commit()

        events = set()
        for event_id, asset_id, state_time, pit_fraction, entity_id in rows:
            events.add(str(event_id))
            result["event_asset_pairs"] += 1
            if entity_id is None:
                result["missing_asset_entity_pairs"] += 1
                continue

            pit = int(float(pit_fraction or 0.0) >= 0.999999)
            if pit:
                result["pit_links"] += 1
            else:
                result["non_pit_links"] += 1

            link_id = stable_id(
                "eel",
                event_id,
                entity_id,
                "direct_asset_subject",
                state_time,
                link_version,
            )
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO event_entity_links_v001(
                    event_entity_link_id,event_id,entity_id,asset_id,
                    link_role,first_available_at,availability_basis,
                    availability_is_point_in_time,source_kind,source_ref,
                    link_version,link_run_id,metadata_json
                ) VALUES (?,?,?,?,?,?,?,?,?,?,?,?,?)
                """,
                (
                    link_id,
                    str(event_id),
                    int(entity_id),
                    int(asset_id),
                    "direct_asset_subject",
                    str(state_time),
                    "normalized_event_state_first_state_time",
                    pit,
                    "normalized_event_state_snapshot",
                    f"{event_id}:{asset_id}:{event_feature_version}",
                    link_version,
                    run_id,
                    json.dumps(
                        {
                            "event_feature_version": event_feature_version,
                            "pit_fraction_at_first_state": float(
                                pit_fraction or 0.0
                            ),
                        },
                        sort_keys=True,
                    ),
                ),
            )
            result["links_written"] += max(0, int(cur.rowcount))

        result["events"] = len(events)
        if result["missing_asset_entity_pairs"]:
            result["status"] = "FAIL"
            result["failures"].append(
                "direct_event_asset_without_entity_mapping"
            )

        total_run_links = conn.execute(
            """
            SELECT COUNT(*)
            FROM event_entity_links_v001
            WHERE link_run_id=?
            """,
            (run_id,),
        ).fetchone()[0]
        result["links_present_for_run"] = int(total_run_links)

        conn.execute(
            """
            UPDATE event_entity_link_runs_v001
            SET finished_at=?,status=?,links_written=?,error_json=?
            WHERE link_run_id=?
            """,
            (
                utc_now(),
                "completed" if result["status"] == "PASS" else "failed",
                int(total_run_links),
                (
                    None
                    if not result["failures"]
                    else json.dumps(result["failures"])
                ),
                run_id,
            ),
        )
        conn.commit()

    return result

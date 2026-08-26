from __future__ import annotations

import hashlib
import json
import sqlite3
from pathlib import Path


def _normalized_name(name: str | None, ticker: str) -> str:
    base = " ".join(str(name or "").split()).strip()
    if not base:
        base = ticker
    # A proxy is intentionally asset-specific. This avoids falsely claiming
    # that share classes / ticker aliases are already resolved to one issuer.
    return f"{base} [{ticker}]"


def seed_asset_entity_proxies(
    db: Path,
    *,
    asset_type: str = "equity",
    active: int = 1,
) -> dict:
    result = {
        "status": "PASS",
        "eligible_assets": 0,
        "existing_mappings": 0,
        "proxy_entities_created": 0,
        "asset_entity_links_created": 0,
        "failures": [],
    }

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        rows = conn.execute(
            """
            SELECT asset_id,ticker,name
            FROM assets
            WHERE asset_type=? AND active=?
            ORDER BY asset_id
            """,
            (asset_type, active),
        ).fetchall()
        result["eligible_assets"] = len(rows)

        for asset_id, ticker, name in rows:
            existing = conn.execute(
                "SELECT entity_id FROM asset_entities WHERE asset_id=?",
                (asset_id,),
            ).fetchone()
            if existing is not None:
                result["existing_mappings"] += 1
                continue

            canonical = _normalized_name(name, str(ticker))
            metadata = json.dumps(
                {
                    "foundation": "event_graph_brain_foundation_v001",
                    "proxy_for_asset_id": int(asset_id),
                    "ticker_at_seed_time": str(ticker),
                    "resolved_issuer": False,
                    "note": (
                        "Asset-specific graph proxy only. Do not treat as "
                        "resolved corporate issuer identity."
                    ),
                },
                sort_keys=True,
            )

            entity = conn.execute(
                """
                SELECT entity_id
                FROM entities
                WHERE entity_type='listed_asset_proxy'
                  AND canonical_name=?
                """,
                (canonical,),
            ).fetchone()
            if entity is None:
                cur = conn.execute(
                    """
                    INSERT INTO entities(
                        entity_type,canonical_name,external_id,country,
                        metadata_json
                    ) VALUES ('listed_asset_proxy',?,?,NULL,?)
                    """,
                    (
                        canonical,
                        f"asset_id:{int(asset_id)}",
                        metadata,
                    ),
                )
                entity_id = int(cur.lastrowid)
                result["proxy_entities_created"] += 1
            else:
                entity_id = int(entity[0])

            conn.execute(
                """
                INSERT INTO asset_entities(asset_id,entity_id)
                VALUES (?,?)
                """,
                (int(asset_id), entity_id),
            )
            result["asset_entity_links_created"] += 1

        conn.commit()

        missing = conn.execute(
            """
            SELECT COUNT(*)
            FROM assets a
            LEFT JOIN asset_entities ae ON ae.asset_id=a.asset_id
            WHERE a.asset_type=? AND a.active=?
              AND ae.asset_id IS NULL
            """,
            (asset_type, active),
        ).fetchone()[0]

    if int(missing) != 0:
        result["status"] = "FAIL"
        result["failures"].append(
            f"eligible_assets_without_entity_mapping={int(missing)}"
        )
    return result

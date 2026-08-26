from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass


def normalize_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = (
        text.replace("\u00a0", " ")
        .replace("’", "'")
        .replace("“", '"')
        .replace("”", '"')
    )
    text = re.sub(r"\s+", " ", text).strip(" \t\r\n,;:.")
    return text.casefold()


@dataclass(frozen=True)
class Resolution:
    status: str
    entity_id: int | None
    method: str | None
    alias: str | None


class ExactResolverV002:
    """
    Conservative resolution only.

    Aliases are annotated by provenance. If the same normalized alias maps to
    multiple entities, it is ambiguous and never auto-resolved.
    """

    def __init__(self, conn: sqlite3.Connection):
        aliases: dict[str, set[int]] = defaultdict(set)
        labels: dict[tuple[str, int], str] = {}

        for entity_id, canonical_name in conn.execute(
            """
            SELECT entity_id,canonical_name
            FROM entities
            WHERE canonical_name IS NOT NULL
            """
        ):
            key = normalize_name(canonical_name)
            if key:
                aliases[key].add(int(entity_id))
                labels[(key, int(entity_id))] = str(canonical_name)

        for ticker, name, entity_id in conn.execute(
            """
            SELECT a.ticker,a.name,ae.entity_id
            FROM assets a
            JOIN asset_entities ae ON ae.asset_id=a.asset_id
            """
        ):
            for raw in (ticker, name):
                if raw is None:
                    continue
                key = normalize_name(raw)
                if key:
                    aliases[key].add(int(entity_id))
                    labels[(key, int(entity_id))] = str(raw)

        self.aliases = dict(aliases)
        self.labels = labels

    def resolve(self, raw: str) -> Resolution:
        key = normalize_name(raw)
        ids = sorted(self.aliases.get(key, set()))
        if not ids:
            return Resolution("unresolved", None, None, None)
        if len(ids) > 1:
            return Resolution("ambiguous_exact", None, None, None)
        eid = ids[0]
        return Resolution(
            "resolved_exact",
            eid,
            "exact_unique_existing_alias",
            self.labels.get((key, eid)),
        )

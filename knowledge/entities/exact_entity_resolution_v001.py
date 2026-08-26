from __future__ import annotations

import re
import sqlite3
import unicodedata
from collections import defaultdict
from dataclasses import dataclass


SPACE = re.compile(r"\s+")
PUNCT_SPACE = re.compile(r"\s*([,&])\s*")


def normalize_entity_name(value: str) -> str:
    text = unicodedata.normalize("NFKC", str(value or ""))
    text = text.replace("\u00a0", " ")
    text = text.replace("’", "'").replace("“", '"').replace("”", '"')
    text = text.strip()
    text = SPACE.sub(" ", text)
    text = PUNCT_SPACE.sub(r"\1 ", text)
    text = text.strip(" \t\r\n,;:.")
    return text.casefold()


@dataclass(frozen=True)
class Resolution:
    status: str
    entity_id: int | None
    matched_alias: str | None
    method: str | None


class ExactEntityResolver:
    """
    Conservative resolver.

    No fuzzy auto-resolution.
    No legal-suffix stripping.
    No entity creation.

    An alias resolves only when its normalized form maps to exactly one
    existing entity.
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
            key = normalize_entity_name(canonical_name)
            if key:
                aliases[key].add(int(entity_id))
                labels[(key, int(entity_id))] = str(canonical_name)

        # Asset names/tickers may safely resolve only through their already
        # established asset_entities mapping.
        for asset_id, ticker, name, entity_id in conn.execute(
            """
            SELECT a.asset_id,a.ticker,a.name,ae.entity_id
            FROM assets a
            JOIN asset_entities ae ON ae.asset_id=a.asset_id
            """
        ):
            for raw in (name, ticker):
                if raw is None:
                    continue
                key = normalize_entity_name(str(raw))
                if key:
                    aliases[key].add(int(entity_id))
                    labels[(key, int(entity_id))] = str(raw)

        self.aliases = dict(aliases)
        self.labels = labels

    def resolve(self, raw_name: str) -> Resolution:
        key = normalize_entity_name(raw_name)
        ids = sorted(self.aliases.get(key, set()))
        if not ids:
            return Resolution("unresolved", None, None, None)
        if len(ids) > 1:
            return Resolution("ambiguous_exact", None, None, None)
        entity_id = ids[0]
        return Resolution(
            "resolved_exact",
            entity_id,
            self.labels.get((key, entity_id)),
            "exact_unique_alias",
        )

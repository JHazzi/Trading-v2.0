from __future__ import annotations

import json
import re
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


CONTRACT_VERSION = "information_capture_semantic_identity_v0012"


def _conn(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


def canonical_period_scope(metadata_json: str | None) -> str:
    try:
        meta = json.loads(metadata_json or "{}")
    except json.JSONDecodeError:
        meta = {}
    explicit = meta.get("period_scope")
    if explicit:
        return str(explicit)
    horizon = str(meta.get("provider_horizon") or "").strip().lower().replace("_", " ")
    if "quarter" in horizon:
        return "fiscal_quarter"
    if "year" in horizon:
        return "fiscal_year"
    if not horizon:
        return "unknown"
    return "provider:" + re.sub(r"[^a-z0-9]+", "_", horizon).strip("_")


def quality_audit(db_path: Path) -> dict[str, Any]:
    with _conn(db_path) as conn:
        rows = conn.execute(
            "SELECT asset_ticker, expectation_type, metric_key, fiscal_period, statistic_key, "
            "value_real, value_text, available_at, source_observation_id, metadata_json "
            "FROM expectation_observations ORDER BY asset_ticker, available_at, observation_id"
        ).fetchall()
        orphan = conn.execute(
            "SELECT COUNT(*) FROM expectation_observations e LEFT JOIN source_observations s "
            "ON e.source_observation_id=s.observation_id WHERE s.observation_id IS NULL"
        ).fetchone()[0]
        invalid = conn.execute(
            "SELECT COUNT(*) FROM source_observations WHERE strict_pit=1 "
            "AND datetime(available_at) < datetime(retrieved_at)"
        ).fetchone()[0]

    metric_counts = Counter()
    statistic_counts = Counter()
    scope_counts = Counter()
    source_field_counts = Counter()
    per_symbol_counts = Counter()
    snapshots_by_symbol: dict[str, set[str]] = defaultdict(set)

    # Coarse identity reproduces the V001/V0011 diagnostic; scoped identity is the
    # canonical V0012 series identity.
    coarse_members: dict[tuple[str, ...], list[str]] = defaultdict(list)
    scoped_counts = Counter()

    for r in rows:
        sym = str(r["asset_ticker"] or "")
        source_id = str(r["source_observation_id"])
        per_symbol_counts[sym] += 1
        snapshots_by_symbol[sym].add(source_id)
        metric_counts[str(r["metric_key"])] += 1
        statistic_counts[str(r["statistic_key"])] += 1
        scope = canonical_period_scope(r["metadata_json"])
        scope_counts[scope] += 1

        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        sf = meta.get("provider_source_field")
        if sf not in (None, ""):
            source_field_counts[str(sf)] += 1

        coarse = (
            sym, source_id, str(r["expectation_type"]), str(r["metric_key"]),
            str(r["fiscal_period"] or ""), str(r["statistic_key"]),
        )
        scoped = coarse + (scope,)
        coarse_members[coarse].append(scope)
        scoped_counts[scoped] += 1

    coarse_collisions = {k: v for k, v in coarse_members.items() if len(v) > 1}
    canonical_collisions = {k: n for k, n in scoped_counts.items() if n > 1}
    cross_scope_overlaps = {
        k: scopes for k, scopes in coarse_collisions.items()
        if len(set(scopes)) > 1 and all(scoped_counts[k + (s,)] == 1 for s in set(scopes))
    }

    per_symbol = {
        sym: {
            "expectation_rows": per_symbol_counts[sym],
            "source_snapshots": len(snapshots_by_symbol[sym]),
        }
        for sym in sorted(per_symbol_counts)
    }
    normalized_counts = [x["expectation_rows"] for x in per_symbol.values()]
    identical = len(normalized_counts) > 1 and len(set(normalized_counts)) == 1

    integrity_ok = int(orphan) == 0 and int(invalid) == 0
    semantic_ok = len(canonical_collisions) == 0
    warnings: list[str] = []
    if identical:
        warnings.append("IDENTICAL_NORMALIZED_ROW_COUNT_ACROSS_SYMBOLS_SCHEMA_SHAPE")
    if cross_scope_overlaps:
        warnings.append("CROSS_SCOPE_SAME_FISCAL_DATE_OVERLAPS_EXPECTED")
    if canonical_collisions:
        warnings.append("CANONICAL_SERIES_COLLISIONS_PRESENT")

    examples = []
    for k, scopes in list(cross_scope_overlaps.items())[:20]:
        examples.append({
            "symbol": k[0],
            "source_observation_id": k[1],
            "expectation_type": k[2],
            "metric_key": k[3],
            "fiscal_period": k[4],
            "statistic_key": k[5],
            "period_scopes": sorted(set(scopes)),
            "rows": len(scopes),
        })

    return {
        "status": "PASS" if integrity_ok else "FAIL",
        "contract_version": CONTRACT_VERSION,
        "feature_transform_readiness": "PASS" if integrity_ok and semantic_ok else "BLOCKED",
        "expectation_rows": len(rows),
        "symbols": len(per_symbol),
        "orphan_expectation_rows": int(orphan),
        "invalid_strict_source_time_rows": int(invalid),
        "per_symbol": per_symbol,
        "metric_counts": dict(sorted(metric_counts.items())),
        "statistic_counts": dict(sorted(statistic_counts.items())),
        "period_scope_counts": dict(sorted(scope_counts.items())),
        "provider_source_field_counts": dict(sorted(source_field_counts.items())),
        "coarse_same_snapshot_collision_count": len(coarse_collisions),
        "cross_scope_same_fiscal_date_overlap_count": len(cross_scope_overlaps),
        "canonical_same_snapshot_series_collision_count": len(canonical_collisions),
        "cross_scope_overlap_examples": examples,
        "identical_normalized_row_count_across_symbols": identical,
        "warnings": warnings,
        "interpretation": (
            "The 287-row shape is provider/schema structure, not evidence of duplication by itself. "
            "Fiscal-quarter and fiscal-year estimates can legitimately share the same fiscalDateEnding. "
            "V0012 therefore treats period_scope as part of series identity. Existing rows remain immutable; "
            "their scope is recovered from provider_horizon metadata."
        ),
    }


def revision_diff(db_path: Path, symbol: str) -> dict[str, Any]:
    symbol = symbol.upper().strip()
    with _conn(db_path) as conn:
        sources = conn.execute(
            "SELECT DISTINCT source_observation_id, available_at FROM expectation_observations "
            "WHERE asset_ticker=? ORDER BY available_at DESC",
            (symbol,),
        ).fetchall()
        if len(sources) < 2:
            return {
                "status": "INSUFFICIENT_SNAPSHOTS",
                "contract_version": CONTRACT_VERSION,
                "symbol": symbol,
                "snapshots": len(sources),
            }
        latest, previous = sources[0], sources[1]

        def load(source_id: str) -> dict[tuple[str, str, str, str, str], list[tuple[Any, Any]]]:
            out: dict[tuple[str, str, str, str, str], list[tuple[Any, Any]]] = defaultdict(list)
            rs = conn.execute(
                "SELECT expectation_type, metric_key, COALESCE(fiscal_period,''), statistic_key, "
                "value_real, value_text, metadata_json "
                "FROM expectation_observations WHERE asset_ticker=? AND source_observation_id=?",
                (symbol, source_id),
            ).fetchall()
            for r in rs:
                scope = canonical_period_scope(r[6])
                k = (str(r[0]), scope, str(r[1]), str(r[2]), str(r[3]))
                out[k].append((r[4], r[5]))
            return out

        previous_map = load(str(previous["source_observation_id"]))
        latest_map = load(str(latest["source_observation_id"]))

    keys = set(previous_map) | set(latest_map)
    changed = unchanged = added = removed = ambiguous = 0
    changes = []
    for k in sorted(keys):
        av, bv = previous_map.get(k), latest_map.get(k)
        if av is None:
            added += 1
            continue
        if bv is None:
            removed += 1
            continue
        if len(av) != 1 or len(bv) != 1:
            ambiguous += 1
            continue
        if av[0] == bv[0]:
            unchanged += 1
        else:
            changed += 1
            if len(changes) < 30:
                changes.append({
                    "series": {
                        "expectation_type": k[0],
                        "period_scope": k[1],
                        "metric_key": k[2],
                        "fiscal_period": k[3],
                        "statistic_key": k[4],
                    },
                    "previous": av[0],
                    "latest": bv[0],
                })

    return {
        "status": "PASS" if ambiguous == 0 else "PASS_WITH_AMBIGUOUS_SERIES",
        "contract_version": CONTRACT_VERSION,
        "symbol": symbol,
        "previous_available_at": previous["available_at"],
        "latest_available_at": latest["available_at"],
        "changed_series": changed,
        "unchanged_series": unchanged,
        "added_series": added,
        "removed_series": removed,
        "ambiguous_series": ambiguous,
        "change_examples": changes,
        "series_identity": [
            "expectation_type", "period_scope", "metric_key", "fiscal_period", "statistic_key"
        ],
        "note": (
            "period_scope is part of identity. Quarterly and annual consensus values sharing a fiscal "
            "ending date are distinct series and are never treated as revisions of one another."
        ),
    }

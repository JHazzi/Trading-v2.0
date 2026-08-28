from __future__ import annotations

import json
import sqlite3
from collections import Counter, defaultdict
from pathlib import Path
from typing import Any


def _conn(db_path: Path) -> sqlite3.Connection:
    c = sqlite3.connect(str(db_path))
    c.row_factory = sqlite3.Row
    return c


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

    per_symbol: dict[str, dict[str, Any]] = {}
    metric_counts = Counter()
    statistic_counts = Counter()
    horizon_counts = Counter()
    source_field_counts = Counter()
    snapshot_series = Counter()
    source_counts = Counter()
    snapshots_by_symbol: dict[str, set[str]] = defaultdict(set)

    for r in rows:
        sym = str(r["asset_ticker"] or "")
        source_counts[sym] += 1
        snapshots_by_symbol[sym].add(str(r["source_observation_id"]))
        metric_counts[str(r["metric_key"])] += 1
        statistic_counts[str(r["statistic_key"])] += 1
        key = (
            sym,
            str(r["source_observation_id"]),
            str(r["expectation_type"]),
            str(r["metric_key"]),
            str(r["fiscal_period"] or ""),
            str(r["statistic_key"]),
        )
        snapshot_series[key] += 1
        try:
            meta = json.loads(r["metadata_json"] or "{}")
        except json.JSONDecodeError:
            meta = {}
        horizon = meta.get("provider_horizon")
        if horizon not in (None, ""):
            horizon_counts[str(horizon)] += 1
        sf = meta.get("provider_source_field")
        if sf not in (None, ""):
            source_field_counts[str(sf)] += 1

    for sym in sorted(source_counts):
        per_symbol[sym] = {
            "expectation_rows": source_counts[sym],
            "source_snapshots": len(snapshots_by_symbol[sym]),
        }

    collisions = [
        {
            "symbol": k[0], "source_observation_id": k[1], "expectation_type": k[2],
            "metric_key": k[3], "fiscal_period": k[4], "statistic_key": k[5], "rows": n,
        }
        for k, n in snapshot_series.items() if n > 1
    ]
    normalized_counts = [v["expectation_rows"] for v in per_symbol.values()]
    identical = len(normalized_counts) > 1 and len(set(normalized_counts)) == 1
    status = "PASS" if orphan == 0 and invalid == 0 else "FAIL"
    warnings = []
    if identical:
        warnings.append("IDENTICAL_NORMALIZED_ROW_COUNT_ACROSS_SYMBOLS")
    if collisions:
        warnings.append("SAME_SNAPSHOT_SERIES_COLLISIONS_PRESENT")

    return {
        "status": status,
        "expectation_rows": len(rows),
        "symbols": len(per_symbol),
        "orphan_expectation_rows": int(orphan),
        "invalid_strict_source_time_rows": int(invalid),
        "per_symbol": per_symbol,
        "metric_counts": dict(sorted(metric_counts.items())),
        "statistic_counts": dict(sorted(statistic_counts.items())),
        "provider_horizon_counts": dict(sorted(horizon_counts.items())),
        "provider_source_field_counts": dict(sorted(source_field_counts.items())),
        "same_snapshot_series_collision_count": len(collisions),
        "same_snapshot_series_collision_examples": collisions[:20],
        "identical_normalized_row_count_across_symbols": identical,
        "warnings": warnings,
        "interpretation": (
            "Warnings are diagnostics, not failures. In particular, identical row counts may be a provider-schema property. "
            "Same-snapshot series collisions must be understood before any feature transformer treats rows as revisions."
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
            return {"status": "INSUFFICIENT_SNAPSHOTS", "symbol": symbol, "snapshots": len(sources)}
        latest, previous = sources[0], sources[1]

        def load(source_id: str) -> dict[tuple[str, str, str, str], list[tuple[Any, Any]]]:
            out: dict[tuple[str, str, str, str], list[tuple[Any, Any]]] = defaultdict(list)
            rs = conn.execute(
                "SELECT expectation_type, metric_key, COALESCE(fiscal_period,''), statistic_key, value_real, value_text "
                "FROM expectation_observations WHERE asset_ticker=? AND source_observation_id=?",
                (symbol, source_id),
            ).fetchall()
            for r in rs:
                k = (str(r[0]), str(r[1]), str(r[2]), str(r[3]))
                out[k].append((r[4], r[5]))
            return out

        a = load(str(previous["source_observation_id"]))
        b = load(str(latest["source_observation_id"]))

    keys = set(a) | set(b)
    changed = unchanged = added = removed = ambiguous = 0
    changes = []
    for k in sorted(keys):
        av, bv = a.get(k), b.get(k)
        if av is None:
            added += 1; continue
        if bv is None:
            removed += 1; continue
        if len(av) != 1 or len(bv) != 1:
            ambiguous += 1; continue
        if av[0] == bv[0]:
            unchanged += 1
        else:
            changed += 1
            if len(changes) < 30:
                changes.append({"series": k, "previous": av[0], "latest": bv[0]})
    return {
        "status": "PASS",
        "symbol": symbol,
        "previous_available_at": previous["available_at"],
        "latest_available_at": latest["available_at"],
        "changed_series": changed,
        "unchanged_series": unchanged,
        "added_series": added,
        "removed_series": removed,
        "ambiguous_series": ambiguous,
        "change_examples": changes,
        "note": "Only one-row-per-series snapshots are diffed. Ambiguous same-snapshot collisions are never silently treated as temporal revisions.",
    }

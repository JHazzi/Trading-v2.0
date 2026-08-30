"""Materialize horizon-conditioned terminal outcomes without training a model.

The canonical market source and Market V003 Core are opened read-only.  The
destination is built in a temporary SQLite file and atomically published only
after exact H1/H3/H5/H10 parity and structural integrity pass.  Long-horizon
raw-close outcomes remain blocked for training pending selection review.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter
from contextlib import closing
import hashlib
import json
import math
import os
from pathlib import Path
import sqlite3
import tempfile
from typing import Any, Sequence

ROOT = Path(__file__).resolve().parents[1]
DEFAULT_SOURCE = ROOT / "data" / "database" / "market_data_v2.db"
DEFAULT_CORE = ROOT / "data" / "processed" / "market_daily_v003_core.db"
DEFAULT_OUTPUT = ROOT / "data" / "processed" / "market_temporal_v001.db"
DEFAULT_CONFIG = ROOT / "config" / "temporal_dataset_v001.json"
DEFAULT_REPORT_DIR = ROOT / "reports" / "market_temporal_v001"
QUALITY_VIEW = "daily_price_quality_gated_observations_v002"
PARITY_HORIZONS = (1, 3, 5, 10)
PARITY_FIELDS = (
    "target_trading_day",
    "return_pct",
    "corporate_action_overlap",
    "label_status",
)

SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
CREATE TABLE dataset_horizons(
 tau_sessions INTEGER PRIMARY KEY CHECK(tau_sessions BETWEEN 1 AND 252),
 materialized INTEGER NOT NULL CHECK(materialized IN (0,1)),
 role_json TEXT NOT NULL
);
CREATE TABLE temporal_price_points(
 asset_id INTEGER NOT NULL,asset_session_index INTEGER NOT NULL,
 trading_day TEXT NOT NULL,bar_end_utc TEXT NOT NULL,raw_close REAL NOT NULL,
 price_observation_id TEXT NOT NULL,observation_sequence INTEGER NOT NULL,
 observed_at TEXT NOT NULL,causal_available_at TEXT NOT NULL,
 PRIMARY KEY(asset_id,asset_session_index),UNIQUE(asset_id,trading_day)
) WITHOUT ROWID;
CREATE TABLE temporal_origins(
 origin_id INTEGER PRIMARY KEY,state_id TEXT NOT NULL UNIQUE,
 asset_id INTEGER NOT NULL,ticker TEXT NOT NULL,sector TEXT NOT NULL,
 origin_trading_day TEXT NOT NULL,state_time TEXT NOT NULL,
 origin_session_index INTEGER NOT NULL,raw_close_origin REAL NOT NULL,
 market_feature_version TEXT NOT NULL,
 UNIQUE(asset_id,origin_trading_day),
 FOREIGN KEY(asset_id,origin_session_index)
   REFERENCES temporal_price_points(asset_id,asset_session_index)
);
CREATE TABLE temporal_corporate_actions(
 asset_id INTEGER NOT NULL,effective_trading_day TEXT NOT NULL,
 action_type TEXT NOT NULL,action_observation_id TEXT NOT NULL,
 corporate_action_version_id TEXT NOT NULL,observed_at TEXT NOT NULL,
 is_present INTEGER NOT NULL CHECK(is_present=1),
 PRIMARY KEY(asset_id,effective_trading_day,action_type)
) WITHOUT ROWID;
CREATE TABLE temporal_outcomes(
 origin_id INTEGER NOT NULL REFERENCES temporal_origins(origin_id),
 tau_sessions INTEGER NOT NULL REFERENCES dataset_horizons(tau_sessions),
 target_trading_day TEXT,return_pct REAL,corporate_action_overlap INTEGER NOT NULL
   CHECK(corporate_action_overlap IN (0,1)),
 label_status TEXT NOT NULL CHECK(label_status IN
   ('usable','corporate_action_overlap','insufficient_future')),
 label_version TEXT NOT NULL,
 PRIMARY KEY(origin_id,tau_sessions)
) WITHOUT ROWID;
CREATE TABLE parity_mismatches(
 mismatch_number INTEGER PRIMARY KEY,state_id TEXT,asset_id INTEGER,
 origin_trading_day TEXT,tau_sessions INTEGER,field_name TEXT,
 expected_json TEXT,observed_json TEXT,reason TEXT NOT NULL
);
CREATE TABLE selection_by_horizon(
 tau_sessions INTEGER PRIMARY KEY,total_origins INTEGER NOT NULL,
 resolved_origins INTEGER NOT NULL,usable_origins INTEGER NOT NULL,
 corporate_action_overlap_origins INTEGER NOT NULL,
 insufficient_future_origins INTEGER NOT NULL,
 overlap_fraction_resolved REAL,usable_fraction_all REAL
);
CREATE TABLE selection_by_asset(
 tau_sessions INTEGER NOT NULL,asset_id INTEGER NOT NULL,ticker TEXT NOT NULL,
 sector TEXT NOT NULL,total_origins INTEGER NOT NULL,resolved_origins INTEGER NOT NULL,
 usable_origins INTEGER NOT NULL,corporate_action_overlap_origins INTEGER NOT NULL,
 insufficient_future_origins INTEGER NOT NULL,overlap_fraction_resolved REAL,
 PRIMARY KEY(tau_sessions,asset_id)
);
CREATE TABLE selection_by_sector(
 tau_sessions INTEGER NOT NULL,sector TEXT NOT NULL,total_origins INTEGER NOT NULL,
 resolved_origins INTEGER NOT NULL,usable_origins INTEGER NOT NULL,
 corporate_action_overlap_origins INTEGER NOT NULL,
 insufficient_future_origins INTEGER NOT NULL,overlap_fraction_resolved REAL,
 PRIMARY KEY(tau_sessions,sector)
);
CREATE TABLE selection_by_year(
 tau_sessions INTEGER NOT NULL,origin_year TEXT NOT NULL,total_origins INTEGER NOT NULL,
 resolved_origins INTEGER NOT NULL,usable_origins INTEGER NOT NULL,
 corporate_action_overlap_origins INTEGER NOT NULL,
 insufficient_future_origins INTEGER NOT NULL,overlap_fraction_resolved REAL,
 PRIMARY KEY(tau_sessions,origin_year)
);
CREATE TABLE training_gate(
 gate_name TEXT PRIMARY KEY,status TEXT NOT NULL,authorized INTEGER NOT NULL
   CHECK(authorized=0),reason TEXT NOT NULL
);
CREATE VIEW market_temporal_v001_outcomes AS
SELECT s.state_id,s.asset_id,s.ticker,s.sector,
       s.origin_trading_day,o.target_trading_day,o.tau_sessions,o.return_pct,
       o.corporate_action_overlap,o.label_status,o.label_version
FROM temporal_outcomes o JOIN temporal_origins s USING(origin_id);
"""


class BuildBlocked(RuntimeError):
    """The candidate artifact was not published because a hard gate failed."""


def canonical(value: Any) -> str:
    return json.dumps(
        value, sort_keys=True, ensure_ascii=False, separators=(",", ":"),
        allow_nan=False,
    )


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode("utf-8")).hexdigest()


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def file_state(path: Path) -> dict[str, list[int] | None]:
    state: dict[str, list[int] | None] = {}
    for suffix in ("", "-wal", "-journal"):
        item = Path(str(path) + suffix)
        stat = item.stat() if item.exists() else None
        state[suffix or "main"] = (
            None if stat is None else [int(stat.st_size), int(stat.st_mtime_ns)]
        )
    return state


def ro_connect(path: Path) -> sqlite3.Connection:
    resolved = path.expanduser().resolve()
    conn = sqlite3.connect(resolved.as_uri() + "?mode=ro", uri=True, timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    return conn


def _decode(value: Any) -> Any:
    try:
        return json.loads(str(value))
    except (TypeError, json.JSONDecodeError):
        return value


def _metadata(conn: sqlite3.Connection) -> dict[str, Any]:
    return {
        str(row["key"]): _decode(row["value_json"])
        for row in conn.execute("SELECT key,value_json FROM build_metadata")
    }


def _objects(conn: sqlite3.Connection, kind: str) -> set[str]:
    return {
        str(row[0])
        for row in conn.execute("SELECT name FROM sqlite_master WHERE type=?", (kind,))
    }


def load_contract(path: Path) -> dict[str, Any]:
    payload = json.loads(path.read_text(encoding="utf-8"))
    if not isinstance(payload, dict):
        raise ValueError("temporal config root must be an object")
    if payload.get("version") != "market_temporal_dataset_v001":
        raise ValueError("unsupported_temporal_dataset_version")
    source_contract = payload.get("source_contract") or {}
    expected_source = {
        "source_asof_contract": "daily_price_asof_v1",
        "source_asof_mode": "historical_session_close_assumption",
        "state_clock": "exchange_session_close",
        "strict_historical_pit": False,
        "market_feature_version": "market_daily_state_v003_core",
        "existing_label_version": "market_daily_reaction_v003_core",
        "existing_target": "raw_close_t_to_raw_close_t_plus_h",
    }
    if any(source_contract.get(key) != value for key, value in expected_source.items()):
        raise ValueError("unsupported_source_contract")
    raw = payload.get("raw_close_label_contract") or {}
    if (
        raw.get("formula") != "100 * (raw_close_target / raw_close_origin - 1)"
        or raw.get("corporate_action_policy")
        != "exclude_any_present_action_in_open_closed_horizon"
        or raw.get("provider_adjusted_close_allowed_as_silent_substitute") is not False
    ):
        raise ValueError("unsupported_raw_close_contract")
    parity = payload.get("parity_gate") or {}
    if (
        parity.get("required_horizons_sessions") != list(PARITY_HORIZONS)
        or parity.get("compare_fields") != list(PARITY_FIELDS)
        or float(parity.get("return_absolute_tolerance", -1)) != 1e-9
        or parity.get("require_zero_missing_reference_rows") is not True
        or parity.get("training_blocked_if_parity_fails") is not True
    ):
        raise ValueError("unsupported_core_parity_contract")
    materialization = payload.get("materialization_contract") or {}
    expected_materialization = {
        "output_db": "data/processed/market_temporal_v001.db",
        "dataset_contract": "market_temporal_horizon_conditioned_outcomes_v001",
        "label_version": "market_temporal_terminal_return_v001",
        "source_and_core_are_read_only": True,
        "idempotent_rebuild": True,
    }
    if any(materialization.get(key) != value for key, value in expected_materialization.items()):
        raise ValueError("unsupported_materialization_contract")
    hc = payload.get("horizon_contract") or {}
    domain = hc.get("tau_domain") or {}
    if domain != {
        "minimum_sessions": 1,
        "maximum_sessions": 252,
        "unit": "eligible_exchange_sessions",
        "integer_only": True,
    }:
        raise ValueError("unsupported_tau_domain")
    supported = hc.get("supported_materialization_strategies")
    if supported != ["configured_sparse", "configured_plus", "dense_all"]:
        raise ValueError("unsupported_materialization_strategies")
    if hc.get("default_materialization_strategy") != "configured_sparse":
        raise ValueError("configured_sparse_must_remain_v001_default")
    materialized = [int(x) for x in hc.get("materialized_sessions", [])]
    train = [int(x) for x in hc.get("training_anchor_sessions", [])]
    holdout = [int(x) for x in hc.get("temporal_generalization_holdout_sessions", [])]
    if sorted(materialized) != sorted(set(train) | set(holdout)):
        raise ValueError("configured_sparse_must_equal_anchor_holdout_union")
    if set(train) & set(holdout):
        raise ValueError("training_and_holdout_tau_overlap")
    if not set(PARITY_HORIZONS).issubset(materialized):
        raise ValueError("parity_horizons_missing")
    action_horizons = set(payload["corporate_action_gate"]["audit_horizons_sessions"])
    if action_horizons != {21, 63, 126, 252}:
        raise ValueError("unsupported_corporate_action_audit_horizons")
    if not action_horizons.issubset(materialized):
        raise ValueError("corporate_action_audit_horizons_missing")
    guards = payload.get("guards") or {}
    required_false = (
        "training_authorized", "v009_artifacts_loaded_or_modified",
        "v009_fit_used", "source_market_db_mutation_allowed",
        "market_v003_core_mutation_allowed", "event_features_allowed",
        "graph_features_allowed", "random_split_allowed",
    )
    if any(guards.get(key) is not False for key in required_false):
        raise ValueError("temporal_dataset_guard_mismatch")
    return payload


def parse_tau_spec(value: str | None) -> list[int]:
    if not value:
        return []
    output: set[int] = set()
    for token in value.split(","):
        token = token.strip()
        if not token:
            continue
        if "-" in token:
            start_text, end_text = token.split("-", 1)
            start, end = int(start_text), int(end_text)
            if start > end:
                raise ValueError("tau range start exceeds end")
            output.update(range(start, end + 1))
        else:
            output.add(int(token))
    if any(tau < 1 or tau > 252 for tau in output):
        raise ValueError("tau must be an integer in [1,252]")
    return sorted(output)


def resolve_taus(
    cfg: dict[str, Any], strategy: str | None = None,
    extra_taus: Sequence[int] = (),
) -> tuple[str, list[int]]:
    hc = cfg["horizon_contract"]
    selected_strategy = strategy or hc["default_materialization_strategy"]
    if selected_strategy not in hc["supported_materialization_strategies"]:
        raise ValueError("unknown_materialization_strategy")
    configured = set(map(int, hc["materialized_sessions"]))
    extras = set(map(int, extra_taus))
    if any(tau < 1 or tau > 252 for tau in extras):
        raise ValueError("tau must be an integer in [1,252]")
    if selected_strategy == "configured_sparse":
        if extras:
            raise ValueError("extra taus require configured_plus")
        taus = configured
    elif selected_strategy == "configured_plus":
        if not extras:
            raise ValueError("configured_plus requires at least one extra tau")
        taus = configured | extras
    else:
        if extras:
            raise ValueError("dense_all does not accept extra taus")
        taus = set(range(1, 253))
    return selected_strategy, sorted(taus)


def horizon_roles(cfg: dict[str, Any], tau: int) -> list[str]:
    hc = cfg["horizon_contract"]
    roles = []
    if tau in hc["existing_evaluation_sessions"]:
        roles.append("core_parity")
    if tau in hc["training_anchor_sessions"]:
        roles.append("training_anchor_checkpoint")
    if tau in hc["temporal_generalization_holdout_sessions"]:
        roles.append("temporal_generalization_holdout")
    if tau in cfg["corporate_action_gate"]["audit_horizons_sessions"]:
        roles.append("long_horizon_selection_audit")
    if not roles:
        roles.append("auxiliary_tau")
    return roles


def validate_inputs(
    source: sqlite3.Connection, core: sqlite3.Connection, cfg: dict[str, Any],
) -> tuple[dict[str, Any], str]:
    source_tables, source_views = _objects(source, "table"), _objects(source, "view")
    required_tables = {
        "assets", "daily_price_asof_configs", "corporate_action_versions",
        "corporate_action_observations",
    }
    if not required_tables.issubset(source_tables) or QUALITY_VIEW not in source_views:
        raise ValueError("source_schema_does_not_match_core_materializer")
    row = source.execute(
        """SELECT selection_point_in_time_verified,cutoff_column
           FROM daily_price_asof_configs
           WHERE asof_contract_version='daily_price_asof_v1'
             AND mode='historical_session_close_assumption'"""
    ).fetchall()
    if len(row) != 1 or tuple(row[0]) != (0, "available_at"):
        raise ValueError("source_price_asof_contract_mismatch")
    core_tables = _objects(core, "table")
    required_core = {
        "build_metadata", "market_daily_v003_states", "market_daily_v003_labels"
    }
    if not required_core.issubset(core_tables):
        raise ValueError("core_schema_mismatch")
    meta = _metadata(core)
    expected_cfg = cfg["source_contract"]
    core_cfg = meta.get("config")
    if not isinstance(core_cfg, dict):
        raise ValueError("core_config_missing")
    checks = {
        "source_asof_contract": expected_cfg["source_asof_contract"],
        "source_asof_mode": expected_cfg["source_asof_mode"],
        "state_clock": expected_cfg["state_clock"],
        "strict_historical_pit": expected_cfg["strict_historical_pit"],
        "feature_version": expected_cfg["market_feature_version"],
        "label_version": expected_cfg["existing_label_version"],
        "target": expected_cfg["existing_target"],
    }
    if any(core_cfg.get(key) != value for key, value in checks.items()):
        raise ValueError("core_contract_mismatch")
    if Path(str(meta.get("source_db"))).name != "market_data_v2.db":
        raise ValueError("core_source_identity_mismatch")
    cutoff = str(meta.get("state_last_day") or "")
    if not cutoff:
        cutoff = str(
            core.execute("SELECT MAX(trading_day) FROM market_daily_v003_states").fetchone()[0]
        )
    return meta, cutoff


def selected_prices(
    source: sqlite3.Connection, asset_id: int, cutoff_day: str,
) -> list[dict[str, Any]]:
    rows = source.execute(
        f"""WITH eligible AS (
          SELECT g.asset_id,g.trading_day,g.bar_end_utc,g.close,
                 g.price_observation_id,g.observation_sequence,g.observed_at,
                 g.causal_available_at,
                 ROW_NUMBER() OVER (
                   PARTITION BY g.asset_id,g.trading_day
                   ORDER BY g.observation_sequence DESC,julianday(g.observed_at) DESC,
                            g.price_observation_id DESC
                 ) obs_rank
          FROM {QUALITY_VIEW} g JOIN assets a ON a.asset_id=g.asset_id
          WHERE a.active=1 AND a.asset_type='equity' AND g.interval='1d'
            AND g.asset_id=? AND g.trading_day<=?
            AND julianday(g.causal_available_at)<=julianday(g.bar_end_utc)
        )
        SELECT * FROM eligible WHERE obs_rank=1 ORDER BY trading_day""",
        (asset_id, cutoff_day),
    ).fetchall()
    output = [dict(row) for row in rows]
    if len({row["trading_day"] for row in output}) != len(output):
        raise ValueError("duplicate_selected_price_day")
    if any(
        not isinstance(row["close"], (int, float))
        or not math.isfinite(float(row["close"])) or float(row["close"]) <= 0
        for row in output
    ):
        raise ValueError("invalid_selected_raw_close")
    return output


def latest_present_actions(
    source: sqlite3.Connection, asset_id: int,
) -> list[dict[str, Any]]:
    return [dict(row) for row in source.execute(
        """WITH ranked AS (
          SELECT o.asset_id,o.effective_trading_day,o.action_type,
                 o.action_observation_id,o.corporate_action_version_id,
                 o.observed_at,v.is_present,
                 ROW_NUMBER() OVER (
                   PARTITION BY o.asset_id,o.effective_trading_day,o.action_type
                   ORDER BY o.observation_sequence DESC,julianday(o.observed_at) DESC,
                            o.action_observation_id DESC
                 ) rn
          FROM corporate_action_observations o
          JOIN corporate_action_versions v
            ON v.corporate_action_version_id=o.corporate_action_version_id
          WHERE o.asset_id=?
        ) SELECT * FROM ranked WHERE rn=1 AND is_present=1
          ORDER BY effective_trading_day,action_type""",
        (asset_id,),
    )]


def _same(field: str, expected: Any, observed: Any, tolerance: float) -> bool:
    if field == "return_pct":
        if expected is None or observed is None:
            return expected is None and observed is None
        return math.isclose(
            float(expected), float(observed), rel_tol=0.0, abs_tol=tolerance
        )
    return expected == observed


def _record_mismatch(
    summary: dict[str, Any], samples: list[dict[str, Any]], *, state: dict[str, Any],
    tau: int, field: str, expected: Any, observed: Any, reason: str,
) -> None:
    summary["mismatch_counts_by_field"][field] += 1
    if len(samples) < 100:
        samples.append({
            "state_id": state.get("state_id"), "asset_id": state.get("asset_id"),
            "origin_trading_day": state.get("trading_day"), "tau_sessions": tau,
            "field_name": field, "expected": expected, "observed": observed,
            "reason": reason,
        })


def _initial_parity(tolerance: float) -> dict[str, Any]:
    return {
        "version": "market_temporal_v001_core_parity_gate",
        "status": "PENDING", "required_horizons_sessions": list(PARITY_HORIZONS),
        "compare_fields": list(PARITY_FIELDS), "return_absolute_tolerance": tolerance,
        "candidate_rows": 0, "reference_rows": 0, "compared_rows": 0,
        "missing_reference_rows": 0, "missing_candidate_rows": 0,
        "mismatch_counts_by_field": Counter(), "mismatch_samples": [],
        "training_blocked_if_parity_fails": True,
    }


def _finalize_parity(summary: dict[str, Any]) -> dict[str, Any]:
    summary["mismatch_counts_by_field"] = dict(summary["mismatch_counts_by_field"])
    hard = (
        summary["missing_reference_rows"] + summary["missing_candidate_rows"]
        + sum(summary["mismatch_counts_by_field"].values())
    )
    summary["status"] = "PASS" if hard == 0 else "FAIL"
    summary["training_gate"] = (
        "BLOCKED_PENDING_LONG_HORIZON_SELECTION_REVIEW"
        if hard == 0 else "BLOCKED_PARITY_FAILURE"
    )
    return summary


def _insert_selection_tables(
    conn: sqlite3.Connection, audit_horizons: Sequence[int],
) -> None:
    placeholders = ",".join("?" for _ in audit_horizons)
    base = f"""o.tau_sessions IN ({placeholders})"""
    shared = """
      COUNT(*) total_origins,
      SUM(o.label_status!='insufficient_future') resolved_origins,
      SUM(o.label_status='usable') usable_origins,
      SUM(o.label_status='corporate_action_overlap') corporate_action_overlap_origins,
      SUM(o.label_status='insufficient_future') insufficient_future_origins,
      CASE WHEN SUM(o.label_status!='insufficient_future')=0 THEN NULL ELSE
        1.0*SUM(o.label_status='corporate_action_overlap')/
        SUM(o.label_status!='insufficient_future') END overlap_fraction_resolved
    """
    conn.execute(
        f"""INSERT INTO selection_by_horizon
        SELECT o.tau_sessions,{shared},1.0*SUM(o.label_status='usable')/COUNT(*)
        FROM temporal_outcomes o WHERE {base} GROUP BY o.tau_sessions""",
        tuple(audit_horizons),
    )
    conn.execute(
        f"""INSERT INTO selection_by_asset
        SELECT o.tau_sessions,s.asset_id,s.ticker,s.sector,{shared}
        FROM temporal_outcomes o JOIN temporal_origins s USING(origin_id)
        WHERE {base} GROUP BY o.tau_sessions,s.asset_id,s.ticker,s.sector""",
        tuple(audit_horizons),
    )
    conn.execute(
        f"""INSERT INTO selection_by_sector
        SELECT o.tau_sessions,s.sector,{shared}
        FROM temporal_outcomes o JOIN temporal_origins s USING(origin_id)
        WHERE {base} GROUP BY o.tau_sessions,s.sector""",
        tuple(audit_horizons),
    )
    conn.execute(
        f"""INSERT INTO selection_by_year
        SELECT o.tau_sessions,substr(s.origin_trading_day,1,4),{shared}
        FROM temporal_outcomes o JOIN temporal_origins s USING(origin_id)
        WHERE {base} GROUP BY o.tau_sessions,substr(s.origin_trading_day,1,4)""",
        tuple(audit_horizons),
    )


def _selection_report(conn: sqlite3.Connection, cfg: dict[str, Any]) -> dict[str, Any]:
    def rows(table: str) -> list[dict[str, Any]]:
        return [dict(row) for row in conn.execute(f"SELECT * FROM {table}")]
    return {
        "version": "market_temporal_v001_corporate_action_selection_audit",
        "status": "REVIEW_REQUIRED",
        "audit_horizons_sessions": cfg["corporate_action_gate"]["audit_horizons_sessions"],
        "by_horizon": rows("selection_by_horizon"),
        "by_asset": rows("selection_by_asset"),
        "by_sector": rows("selection_by_sector"),
        "by_origin_year": rows("selection_by_year"),
        "raw_close_long_horizon_training_authorized": False,
        "review_question": cfg["corporate_action_gate"]["reason"],
        "next_if_material": cfg["corporate_action_gate"]["if_overlap_is_material"],
    }


def _atomic_text(path: Path, text: str) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(prefix=path.name + ".", suffix=".tmp", dir=path.parent)
    os.close(handle)
    temp_path = Path(temp_name)
    try:
        temp_path.write_text(text, encoding="utf-8")
        os.replace(temp_path, path)
    finally:
        temp_path.unlink(missing_ok=True)


def _write_reports(
    report_dir: Path, manifest: dict[str, Any], parity: dict[str, Any],
    selection: dict[str, Any], audit: dict[str, Any],
) -> None:
    report_dir.mkdir(parents=True, exist_ok=True)
    payloads = {
        "manifest.json": manifest,
        "parity_report.json": parity,
        "selection_report.json": selection,
        "audit.json": audit,
    }
    for name, payload in payloads.items():
        _atomic_text(
            report_dir / name,
            json.dumps(payload, indent=2, ensure_ascii=False, allow_nan=False) + "\n",
        )
    lines = [
        "# Market Temporal Dataset V001", "",
        f"Integrity: {audit['integrity_status']}; parity: {parity['status']}.",
        f"Origins: {audit.get('origin_rows', 0):,}; outcomes: {audit.get('outcome_rows', 0):,}.",
        f"Materialized taus: {manifest.get('materialized_taus', [])}.", "",
        "No model was trained. Source/Core were opened read-only. V009 was not loaded.",
        f"Training gate: {audit.get('training_gate_status', 'BLOCKED')}.", "",
        "## Long-horizon selection", "",
        "| Tau | Resolved | Usable | Corporate-action overlap | Overlap / resolved |",
        "|---:|---:|---:|---:|---:|",
    ]
    for row in selection.get("by_horizon", []):
        fraction = row["overlap_fraction_resolved"]
        rendered = "—" if fraction is None else f"{100*float(fraction):.2f}%"
        lines.append(
            f"| {row['tau_sessions']} | {row['resolved_origins']} | "
            f"{row['usable_origins']} | {row['corporate_action_overlap_origins']} | {rendered} |"
        )
    lines.extend([
        "", "Raw-close long-horizon training remains blocked pending scientific review.",
        "Dense terminal marginals are not a coherent joint path distribution.", "",
    ])
    _atomic_text(report_dir / "AUDIT.md", "\n".join(lines))


def _read_manifest(path: Path) -> dict[str, Any]:
    with closing(ro_connect(path)) as conn:
        row = conn.execute("SELECT value_json FROM metadata WHERE key='manifest'").fetchone()
        if row is None:
            raise ValueError("output_manifest_missing")
        return json.loads(row[0])


def compare_output_to_core(
    output_db: Path, core_db: Path, tolerance: float,
) -> dict[str, Any]:
    summary = _initial_parity(tolerance)
    samples: list[dict[str, Any]] = summary["mismatch_samples"]
    with closing(ro_connect(output_db)) as out, closing(ro_connect(core_db)) as core:
        asset_ids = [int(r[0]) for r in core.execute(
            "SELECT DISTINCT asset_id FROM market_daily_v003_states ORDER BY asset_id"
        )]
        for asset_id in asset_ids:
            states = {
                str(r["state_id"]): dict(r)
                for r in core.execute(
                    "SELECT state_id,asset_id,trading_day FROM market_daily_v003_states "
                    "WHERE asset_id=?", (asset_id,),
                )
            }
            refs = {}
            for row in core.execute(
                "SELECT state_id,asset_id,origin_trading_day,target_trading_day,"
                "horizon_sessions,return_pct,corporate_action_overlap,label_status "
                "FROM market_daily_v003_labels WHERE asset_id=? AND horizon_sessions IN (1,3,5,10)",
                (asset_id,),
            ):
                key = (str(row["state_id"]), int(row["horizon_sessions"]))
                if key in refs:
                    raise ValueError("duplicate_core_parity_label")
                refs[key] = dict(row)
            candidates = {}
            for row in out.execute(
                "SELECT state_id,asset_id,origin_trading_day,target_trading_day,"
                "tau_sessions,return_pct,corporate_action_overlap,label_status "
                "FROM market_temporal_v001_outcomes WHERE asset_id=? "
                "AND tau_sessions IN (1,3,5,10)", (asset_id,),
            ):
                key = (str(row["state_id"]), int(row["tau_sessions"]))
                if key in candidates:
                    raise ValueError("duplicate_candidate_parity_label")
                candidates[key] = dict(row)
            summary["reference_rows"] += len(refs)
            summary["candidate_rows"] += len(candidates)
            expected_keys = {(state_id, tau) for state_id in states for tau in PARITY_HORIZONS}
            missing_ref = expected_keys - set(refs)
            missing_candidate = expected_keys - set(candidates)
            summary["missing_reference_rows"] += len(missing_ref)
            summary["missing_candidate_rows"] += len(missing_candidate)
            for state_id, tau in sorted(missing_ref)[: max(0, 100-len(samples))]:
                _record_mismatch(summary, samples, state=states[state_id], tau=tau,
                                 field="row", expected="core row", observed=None,
                                 reason="missing_reference_row")
            for state_id, tau in sorted(missing_candidate)[: max(0, 100-len(samples))]:
                _record_mismatch(summary, samples, state=states[state_id], tau=tau,
                                 field="row", expected="candidate row", observed=None,
                                 reason="missing_candidate_row")
            for key in sorted(set(refs) & set(candidates)):
                ref, candidate = refs[key], candidates[key]
                state = states[key[0]]
                summary["compared_rows"] += 1
                identity = {
                    "asset_id": (ref["asset_id"], candidate["asset_id"]),
                    "origin_trading_day": (
                        ref["origin_trading_day"], candidate["origin_trading_day"]
                    ),
                }
                for field, (expected, observed) in identity.items():
                    if expected != observed:
                        _record_mismatch(summary, samples, state=state, tau=key[1],
                                         field=field, expected=expected, observed=observed,
                                         reason="identity_mismatch")
                for field in PARITY_FIELDS:
                    if not _same(field, ref[field], candidate[field], tolerance):
                        _record_mismatch(summary, samples, state=state, tau=key[1],
                                         field=field, expected=ref[field],
                                         observed=candidate[field], reason="value_mismatch")
    return _finalize_parity(summary)


def audit_output(output_db: Path, core_db: Path, cfg: dict[str, Any]) -> dict[str, Any]:
    failures: list[str] = []
    with closing(ro_connect(output_db)) as conn:
        required = {
            "metadata", "dataset_horizons", "temporal_price_points",
            "temporal_origins", "temporal_corporate_actions", "temporal_outcomes",
            "parity_mismatches", "selection_by_horizon", "selection_by_asset",
            "selection_by_sector", "selection_by_year", "training_gate",
        }
        if not required.issubset(_objects(conn, "table")):
            raise ValueError("temporal_output_schema_incomplete")
        manifest = json.loads(conn.execute(
            "SELECT value_json FROM metadata WHERE key='manifest'"
        ).fetchone()[0])
        taus = [int(r[0]) for r in conn.execute(
            "SELECT tau_sessions FROM dataset_horizons WHERE materialized=1 ORDER BY 1"
        )]
        origin_rows = int(conn.execute("SELECT COUNT(*) FROM temporal_origins").fetchone()[0])
        outcome_rows = int(conn.execute("SELECT COUNT(*) FROM temporal_outcomes").fetchone()[0])
        price_rows = int(conn.execute("SELECT COUNT(*) FROM temporal_price_points").fetchone()[0])
        expected_outcomes = origin_rows * len(taus)
        if outcome_rows != expected_outcomes:
            failures.append("OUTCOME_CARTESIAN_COVERAGE_MISMATCH")
        bad_status = int(conn.execute(
            "SELECT COUNT(*) FROM temporal_outcomes WHERE label_status NOT IN "
            "('usable','corporate_action_overlap','insufficient_future')"
        ).fetchone()[0])
        if bad_status:
            failures.append("UNKNOWN_LABEL_STATUS")
        bad_insufficient = int(conn.execute(
            "SELECT COUNT(*) FROM temporal_outcomes WHERE label_status='insufficient_future' "
            "AND (target_trading_day IS NOT NULL OR return_pct IS NOT NULL OR corporate_action_overlap!=0)"
        ).fetchone()[0])
        if bad_insufficient:
            failures.append("INSUFFICIENT_FUTURE_ROW_HAS_TARGET")
        selection = _selection_report(conn, cfg)
        gate = dict(conn.execute("SELECT * FROM training_gate").fetchone())
    parity = compare_output_to_core(
        output_db, core_db, float(cfg["parity_gate"]["return_absolute_tolerance"])
    )
    if parity["status"] != "PASS":
        failures.append("CORE_PARITY_FAILED")
    expected_gate = (
        "BLOCKED_PENDING_LONG_HORIZON_SELECTION_REVIEW"
        if parity["status"] == "PASS" else "BLOCKED_PARITY_FAILURE"
    )
    if gate["status"] != expected_gate or int(gate["authorized"]) != 0:
        failures.append("TRAINING_GATE_MISMATCH")
    return {
        "version": "market_temporal_v001_integrity_audit",
        "integrity_status": "PASS" if not failures else "FAIL",
        "failures": sorted(set(failures)), "origin_rows": origin_rows,
        "price_grid_rows": price_rows, "outcome_rows": outcome_rows,
        "materialized_taus": taus, "parity": parity, "selection": selection,
        "training_gate_status": gate["status"], "training_authorized": False,
        "model_training_performed": False, "v009_loaded_or_modified": False,
        "manifest_build_fingerprint": manifest["build_fingerprint"],
    }


def _configure_destination(conn: sqlite3.Connection) -> None:
    conn.execute("PRAGMA journal_mode=OFF")
    conn.execute("PRAGMA synchronous=OFF")
    conn.execute("PRAGMA temp_store=MEMORY")
    conn.executescript(SCHEMA)


def materialize(
    source_db: Path = DEFAULT_SOURCE, core_db: Path = DEFAULT_CORE,
    output_db: Path = DEFAULT_OUTPUT, config_path: Path = DEFAULT_CONFIG,
    report_dir: Path = DEFAULT_REPORT_DIR, *, strategy: str | None = None,
    extra_taus: Sequence[int] = (), force_rebuild: bool = False,
) -> dict[str, Any]:
    paths = [source_db.resolve(), core_db.resolve(), output_db.resolve()]
    if len(set(paths)) != 3:
        raise ValueError("source_core_and_output_must_be_distinct")
    if "v009" in str(source_db).lower() or "v009" in str(core_db).lower():
        raise ValueError("v009_artifacts_are_out_of_scope")
    cfg = load_contract(config_path)
    selected_strategy, taus = resolve_taus(cfg, strategy, extra_taus)
    source_before, core_before = file_state(source_db), file_state(core_db)
    code_sha = file_digest(Path(__file__))
    config_sha = file_digest(config_path)
    build_fingerprint = digest({
        "contract": cfg, "strategy": selected_strategy, "taus": taus,
        "source_file_state": source_before, "core_file_state": core_before,
        "code_sha256": code_sha, "config_sha256": config_sha,
    })
    if output_db.exists() and not force_rebuild:
        previous = _read_manifest(output_db)
        if previous.get("build_fingerprint") != build_fingerprint:
            raise ValueError("existing_output_differs_use_force_rebuild")
        audit = audit_output(output_db, core_db, cfg)
        manifest = previous | {"dataset_sha256": file_digest(output_db), "reused_existing": True}
        _write_reports(report_dir, manifest, audit["parity"], audit["selection"], audit)
        if audit["integrity_status"] != "PASS":
            raise BuildBlocked("existing temporal output failed integrity audit")
        return audit | {"reused_existing": True}

    output_db.parent.mkdir(parents=True, exist_ok=True)
    handle, temp_name = tempfile.mkstemp(
        prefix=output_db.name + ".", suffix=".tmp", dir=output_db.parent
    )
    os.close(handle)
    temp_db = Path(temp_name)
    temp_db.unlink()
    parity = _initial_parity(float(cfg["parity_gate"]["return_absolute_tolerance"]))
    selection: dict[str, Any] = {"status": "NOT_RUN"}
    manifest: dict[str, Any] = {
        "dataset_contract": cfg["materialization_contract"]["dataset_contract"],
        "label_version": cfg["materialization_contract"]["label_version"],
        "build_fingerprint": build_fingerprint, "strategy": selected_strategy,
        "materialized_taus": taus, "configured_training_anchors": cfg["horizon_contract"]["training_anchor_sessions"],
        "configured_generalization_holdouts": cfg["horizon_contract"]["temporal_generalization_holdout_sessions"],
        "source_db": str(source_db.resolve()), "core_db": str(core_db.resolve()),
        "source_file_state_before": source_before, "core_file_state_before": core_before,
        "config_sha256": config_sha, "code_sha256": code_sha,
        "model_version": "NONE_DATA_PREPARATION_ONLY", "training_authorized": False,
        "v009_loaded_or_modified": False,
    }
    try:
        with closing(ro_connect(source_db)) as source, closing(ro_connect(core_db)) as core, closing(
            sqlite3.connect(temp_db)
        ) as dest:
            dest.row_factory = sqlite3.Row
            _configure_destination(dest)
            core_meta, cutoff_day = validate_inputs(source, core, cfg)
            manifest["core_build_metadata"] = core_meta
            manifest["source_gate_status"] = "READY_FOR_MATERIALIZATION"
            manifest["source_cutoff_day"] = cutoff_day
            for tau in range(1, 253):
                dest.execute(
                    "INSERT INTO dataset_horizons VALUES(?,?,?)",
                    (tau, int(tau in taus), canonical(horizon_roles(cfg, tau))),
                )
            asset_ids = [int(r[0]) for r in core.execute(
                "SELECT DISTINCT asset_id FROM market_daily_v003_states ORDER BY asset_id"
            )]
            origin_id = 0
            label_version = cfg["materialization_contract"]["label_version"]
            for asset_number, asset_id in enumerate(asset_ids, start=1):
                states = [dict(r) for r in core.execute(
                    "SELECT state_id,asset_id,ticker,sector,trading_day,state_time,feature_version "
                    "FROM market_daily_v003_states WHERE asset_id=? ORDER BY trading_day",
                    (asset_id,),
                )]
                prices = selected_prices(source, asset_id, cutoff_day)
                by_day = {str(row["trading_day"]): i for i, row in enumerate(prices)}
                price_rows = [(
                    asset_id, index, row["trading_day"], row["bar_end_utc"],
                    float(row["close"]), str(row["price_observation_id"]),
                    int(row["observation_sequence"]), str(row["observed_at"]),
                    str(row["causal_available_at"]),
                ) for index, row in enumerate(prices)]
                dest.executemany(
                    "INSERT INTO temporal_price_points VALUES(?,?,?,?,?,?,?,?,?)", price_rows
                )
                actions = latest_present_actions(source, asset_id)
                dest.executemany(
                    "INSERT INTO temporal_corporate_actions VALUES(?,?,?,?,?,?,?)",
                    [(
                        asset_id, row["effective_trading_day"], row["action_type"],
                        row["action_observation_id"], row["corporate_action_version_id"],
                        row["observed_at"], 1,
                    ) for row in actions],
                )
                action_days = sorted(str(row["effective_trading_day"]) for row in actions)
                refs = {}
                for row in core.execute(
                    "SELECT state_id,asset_id,origin_trading_day,target_trading_day,"
                    "horizon_sessions,return_pct,corporate_action_overlap,label_status "
                    "FROM market_daily_v003_labels WHERE asset_id=? AND horizon_sessions IN (1,3,5,10)",
                    (asset_id,),
                ):
                    key = (str(row["state_id"]), int(row["horizon_sessions"]))
                    if key in refs:
                        raise ValueError("duplicate_core_parity_label")
                    refs[key] = dict(row)
                parity["reference_rows"] += len(refs)
                seen_ref: set[tuple[str, int]] = set()
                origin_rows, outcome_rows = [], []
                for state in states:
                    day = str(state["trading_day"])
                    if day not in by_day:
                        raise ValueError("core_origin_missing_from_exact_source_selection")
                    index = by_day[day]
                    if str(state["state_time"]) != str(prices[index]["bar_end_utc"]):
                        raise ValueError("core_state_time_source_close_mismatch")
                    if state["feature_version"] != cfg["source_contract"]["market_feature_version"]:
                        raise ValueError("market_feature_version_mismatch")
                    origin_id += 1
                    origin_rows.append((
                        origin_id, state["state_id"], asset_id, state["ticker"],
                        state["sector"], day, state["state_time"], index,
                        float(prices[index]["close"]), state["feature_version"],
                    ))
                    for tau in taus:
                        target_index = index + tau
                        if target_index >= len(prices):
                            target_day, return_pct, overlap = None, None, 0
                            status = "insufficient_future"
                        else:
                            target = prices[target_index]
                            target_day = str(target["trading_day"])
                            return_pct = 100.0 * (
                                float(target["close"]) / float(prices[index]["close"]) - 1.0
                            )
                            if not math.isfinite(return_pct):
                                target_day, return_pct, overlap = None, None, 0
                                status = "insufficient_future"
                            else:
                                overlap = int(
                                    bisect_right(action_days, target_day)
                                    - bisect_right(action_days, day) > 0
                                )
                                status = "corporate_action_overlap" if overlap else "usable"
                        outcome_rows.append((
                            origin_id, tau, target_day, return_pct, overlap, status, label_version,
                        ))
                        if tau in PARITY_HORIZONS:
                            parity["candidate_rows"] += 1
                            key = (str(state["state_id"]), tau)
                            ref = refs.get(key)
                            if ref is None:
                                parity["missing_reference_rows"] += 1
                                _record_mismatch(
                                    parity, parity["mismatch_samples"], state=state, tau=tau,
                                    field="row", expected="core row", observed=None,
                                    reason="missing_reference_row",
                                )
                                continue
                            seen_ref.add(key)
                            parity["compared_rows"] += 1
                            candidate = {
                                "target_trading_day": target_day, "return_pct": return_pct,
                                "corporate_action_overlap": overlap, "label_status": status,
                            }
                            for field in PARITY_FIELDS:
                                if not _same(
                                    field, ref[field], candidate[field],
                                    parity["return_absolute_tolerance"],
                                ):
                                    _record_mismatch(
                                        parity, parity["mismatch_samples"], state=state,
                                        tau=tau, field=field, expected=ref[field],
                                        observed=candidate[field], reason="value_mismatch",
                                    )
                missing_candidates = set(refs) - seen_ref
                parity["missing_candidate_rows"] += len(missing_candidates)
                for state_id, tau in sorted(missing_candidates)[: max(
                    0, 100-len(parity["mismatch_samples"])
                )]:
                    state = next(s for s in states if s["state_id"] == state_id)
                    _record_mismatch(
                        parity, parity["mismatch_samples"], state=state, tau=tau,
                        field="row", expected="candidate row", observed=None,
                        reason="missing_candidate_row",
                    )
                dest.executemany(
                    "INSERT INTO temporal_origins VALUES(?,?,?,?,?,?,?,?,?,?)", origin_rows
                )
                dest.executemany(
                    "INSERT INTO temporal_outcomes VALUES(?,?,?,?,?,?,?)", outcome_rows
                )
                if asset_number % 25 == 0 or asset_number == len(asset_ids):
                    print(canonical({
                        "stage": "materialize", "assets_complete": asset_number,
                        "assets_total": len(asset_ids), "origins": origin_id,
                    }), flush=True)
            parity = _finalize_parity(parity)
            for number, row in enumerate(parity["mismatch_samples"], start=1):
                dest.execute(
                    "INSERT INTO parity_mismatches VALUES(?,?,?,?,?,?,?,?,?)",
                    (number, row["state_id"], row["asset_id"], row["origin_trading_day"],
                     row["tau_sessions"], row["field_name"], canonical(row["expected"]),
                     canonical(row["observed"]), row["reason"]),
                )
            _insert_selection_tables(
                dest, cfg["corporate_action_gate"]["audit_horizons_sessions"]
            )
            selection = _selection_report(dest, cfg)
            gate_status = parity["training_gate"]
            gate_reason = (
                "Exact Core parity failed; no temporal training may start."
                if parity["status"] != "PASS" else
                "Parity passed, but long-horizon raw-close selection requires review and a model protocol is not preregistered."
            )
            dest.execute(
                "INSERT INTO training_gate VALUES('temporal_training',?,0,?)",
                (gate_status, gate_reason),
            )
            source_after, core_after = file_state(source_db), file_state(core_db)
            manifest.update({
                "source_file_state_after": source_after,
                "core_file_state_after": core_after,
                "inputs_stable_during_build": (
                    source_before == source_after and core_before == core_after
                ),
                "source_and_core_opened_read_only": True,
                "origin_rows": origin_id,
                "outcome_rows": origin_id * len(taus),
                "parity_status": parity["status"],
                "training_gate_status": gate_status,
            })
            if not manifest["inputs_stable_during_build"]:
                parity["status"] = "FAIL"
                manifest["parity_status"] = "FAIL"
                parity["input_stability_failure"] = True
                manifest["training_gate_status"] = "BLOCKED_INPUTS_CHANGED_DURING_BUILD"
                dest.execute(
                    "UPDATE training_gate SET status='BLOCKED_INPUTS_CHANGED_DURING_BUILD',"
                    "reason='Source or Core file state changed during materialization.' "
                    "WHERE gate_name='temporal_training'"
                )
            dest.execute(
                "INSERT INTO metadata VALUES('manifest',?)", (canonical(manifest),)
            )
            dest.executescript("""
              CREATE INDEX idx_temporal_origin_asset_day
                ON temporal_origins(asset_id,origin_trading_day);
              CREATE INDEX idx_temporal_outcome_tau_status
                ON temporal_outcomes(tau_sessions,label_status);
              CREATE INDEX idx_temporal_outcome_tau_origin
                ON temporal_outcomes(tau_sessions,origin_id);
            """)
            dest.commit()
        if parity["status"] != "PASS":
            audit = {
                "version": "market_temporal_v001_integrity_audit",
                "integrity_status": "FAIL", "failures": ["CORE_PARITY_FAILED"],
                "origin_rows": manifest.get("origin_rows", 0),
                "outcome_rows": manifest.get("outcome_rows", 0),
                "training_gate_status": manifest.get("training_gate_status"),
                "training_authorized": False,
            }
            _write_reports(report_dir, manifest, parity, selection, audit)
            raise BuildBlocked("Temporal Dataset V001 parity/input gate failed")
        audit = audit_output(temp_db, core_db, cfg)
        if audit["integrity_status"] != "PASS":
            _write_reports(report_dir, manifest, audit["parity"], audit["selection"], audit)
            raise BuildBlocked("Temporal Dataset V001 integrity audit failed")
        os.replace(temp_db, output_db)
        manifest = manifest | {
            "dataset_sha256": file_digest(output_db), "reused_existing": False,
        }
        _write_reports(report_dir, manifest, audit["parity"], audit["selection"], audit)
        return audit | {"reused_existing": False, "output_db": str(output_db)}
    finally:
        temp_db.unlink(missing_ok=True)


def build_plan(
    source_db: Path = DEFAULT_SOURCE, core_db: Path = DEFAULT_CORE,
    config_path: Path = DEFAULT_CONFIG, *, strategy: str | None = None,
    extra_taus: Sequence[int] = (),
) -> dict[str, Any]:
    """Cheap, read-only feasibility/size plan; it never creates the output DB."""
    cfg = load_contract(config_path)
    selected_strategy, taus = resolve_taus(cfg, strategy, extra_taus)
    before = {"source": file_state(source_db), "core": file_state(core_db)}
    with closing(ro_connect(source_db)) as source, closing(ro_connect(core_db)) as core:
        metadata, cutoff = validate_inputs(source, core, cfg)
        state_rows = int(core.execute(
            "SELECT COUNT(*) FROM market_daily_v003_states"
        ).fetchone()[0])
        assets = int(core.execute(
            "SELECT COUNT(DISTINCT asset_id) FROM market_daily_v003_states"
        ).fetchone()[0])
        parity_reference_rows = int(core.execute(
            "SELECT COUNT(*) FROM market_daily_v003_labels "
            "WHERE horizon_sessions IN (1,3,5,10)"
        ).fetchone()[0])
    after = {"source": file_state(source_db), "core": file_state(core_db)}
    return {
        "version": "market_temporal_v001_materialization_plan",
        "status": "READY" if before == after else "BLOCKED_INPUT_CHANGED",
        "source_and_core_opened_read_only": True,
        "source_and_core_stable_during_plan": before == after,
        "source_cutoff_day": cutoff, "core_metadata": metadata,
        "state_rows": state_rows, "assets": assets,
        "strategy": selected_strategy, "materialized_taus": taus,
        "materialized_tau_count": len(taus),
        "estimated_outcome_rows": state_rows * len(taus),
        "dense_all_estimated_outcome_rows": state_rows * 252,
        "parity_reference_rows": parity_reference_rows,
        "training_authorized": False, "model_training_performed": False,
        "v009_loaded_or_modified": False,
    }


def require_training_authorized(output_db: Path = DEFAULT_OUTPUT) -> None:
    """Mandatory future-runner hook; V001 data preparation never authorizes fit."""
    with closing(ro_connect(output_db)) as conn:
        row = conn.execute(
            "SELECT status,authorized,reason FROM training_gate "
            "WHERE gate_name='temporal_training'"
        ).fetchone()
    if row is None or int(row["authorized"]) != 1:
        status = "MISSING_GATE" if row is None else row["status"]
        reason = "" if row is None else row["reason"]
        raise RuntimeError(f"temporal training blocked: {status}: {reason}")


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--stage", choices=("plan", "build", "audit"), required=True)
    parser.add_argument("--source-db", type=Path, default=DEFAULT_SOURCE)
    parser.add_argument("--core-db", type=Path, default=DEFAULT_CORE)
    parser.add_argument("--output-db", type=Path, default=DEFAULT_OUTPUT)
    parser.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    parser.add_argument("--report-dir", type=Path, default=DEFAULT_REPORT_DIR)
    parser.add_argument(
        "--strategy", choices=("configured_sparse", "configured_plus", "dense_all")
    )
    parser.add_argument(
        "--taus", help="Extra taus for configured_plus, e.g. 4,11,22-25"
    )
    parser.add_argument("--force-rebuild", action="store_true")
    args = parser.parse_args()
    cfg = load_contract(args.config)
    if args.stage == "audit":
        if args.strategy or args.taus or args.force_rebuild:
            parser.error("strategy/taus/force-rebuild apply only to build")
        audit = audit_output(args.output_db, args.core_db, cfg)
        manifest = _read_manifest(args.output_db) | {
            "dataset_sha256": file_digest(args.output_db), "reused_existing": True,
        }
        _write_reports(
            args.report_dir, manifest, audit["parity"], audit["selection"], audit
        )
        print(json.dumps(audit, indent=2, ensure_ascii=False, allow_nan=False))
        raise SystemExit(0 if audit["integrity_status"] == "PASS" else 2)
    if args.stage == "plan":
        if args.force_rebuild:
            parser.error("force-rebuild applies only to build")
        plan = build_plan(
            args.source_db, args.core_db, args.config, strategy=args.strategy,
            extra_taus=parse_tau_spec(args.taus),
        )
        _atomic_text(args.report_dir / "plan.json", json.dumps(
            plan, indent=2, ensure_ascii=False, allow_nan=False
        ) + "\n")
        print(json.dumps(plan, indent=2, ensure_ascii=False, allow_nan=False))
        raise SystemExit(0 if plan["status"] == "READY" else 2)
    extra = parse_tau_spec(args.taus)
    try:
        result = materialize(
            args.source_db, args.core_db, args.output_db, args.config,
            args.report_dir, strategy=args.strategy, extra_taus=extra,
            force_rebuild=args.force_rebuild,
        )
    except BuildBlocked as exc:
        print(str(exc))
        raise SystemExit(2) from exc
    print(json.dumps(result, indent=2, ensure_ascii=False, allow_nan=False))


if __name__ == "__main__":
    main()

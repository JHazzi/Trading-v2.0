"""Read-only inputs -> new, auditable close-aligned event research dataset.

No fitting, fetching, migrations or V009 artifact loading. The SEC historical
adapter is deliberately narrower than the generic clock algebra below.
"""
from __future__ import annotations

import argparse
from bisect import bisect_right
from collections import Counter, defaultdict
from contextlib import closing
from dataclasses import dataclass
from datetime import date, datetime, timedelta, timezone
import hashlib
import importlib.metadata
import json
import math
from pathlib import Path
import re
import sqlite3
import statistics
import time
from typing import Any, Callable

ROOT = Path(__file__).resolve().parents[2]
DEFAULT_CONFIG = ROOT / "config/distributional_event_dataset_v001.json"
CONTRACT = "distributional_event_close_aligned_v001"
FROZEN_END = "2026-08-24"
SEMANTICS = (
    "observed_fact", "official_statement", "reported_fact", "opinion",
    "forecast", "rumor", "speculation", "correction", "retraction", "mixed", "unknown",
)
EVENT_NUMERIC = (
    "event_count", "unique_evidence_count", "distinct_event_type_count",
    "seconds_since_latest_state", "seconds_since_earliest_state",
    "seconds_since_earliest_known_evidence",
    *(f"semantic_{s}_count" for s in SEMANTICS),
)
TARGETS = ("return_pct", "mfe_pct", "mae_pct", "realized_path_vol_pct")
MARKET_ALLOWLIST = frozenset({
    "asset_return_1d_pct", "asset_return_3d_pct", "asset_return_5d_pct",
    "asset_return_10d_pct", "asset_return_20d_pct", "asset_return_63d_pct",
    "asset_vol_5d_pct", "asset_vol_20d_pct", "asset_vol_63d_pct",
    "asset_range_1d_pct", "asset_volume_ratio_20d", "asset_drawdown_20d_pct",
    "asset_drawdown_63d_pct", "asset_drawdown_252d_pct",
})


def canonical(value: Any) -> str:
    return json.dumps(value, sort_keys=True, ensure_ascii=False,
                      separators=(",", ":"), allow_nan=False)


def digest(value: Any) -> str:
    return hashlib.sha256(canonical(value).encode()).hexdigest()


def diagnostic_tree(value: Any) -> Any:
    """Represent invalid numbers in audit evidence, never in model features."""
    if isinstance(value, float) and not math.isfinite(value):
        return {"invalid_nonfinite_number": repr(value)}
    if isinstance(value, dict):
        return {k: diagnostic_tree(v) for k, v in value.items()}
    if isinstance(value, (tuple, list)):
        return [diagnostic_tree(v) for v in value]
    return value


def file_digest(path: Path) -> str:
    with path.open("rb") as stream:
        return hashlib.file_digest(stream, "sha256").hexdigest()


def utc(value: str) -> datetime:
    """Never silently assume UTC/local time/midnight for source timestamps."""
    result = datetime.fromisoformat(str(value).replace("Z", "+00:00"))
    if result.tzinfo is None:
        raise ValueError("timestamp_requires_timezone")
    return result.astimezone(timezone.utc)


def iso(value: datetime) -> str:
    return value.astimezone(timezone.utc).isoformat()


@dataclass(frozen=True)
class EvidenceClock:
    available_at: str
    public_at: str | None
    observed_at: str
    link_available_at: str
    modified_at: str | None = None

    def boundary(self, mode: str) -> datetime:
        clocks = [utc(self.available_at), utc(self.link_available_at)]
        clocks.extend(utc(t) for t in (self.public_at, self.modified_at) if t)
        if mode == "observed_capture":
            clocks.append(utc(self.observed_at))
        elif mode != "historical_public_proxy":
            raise ValueError("unknown_clock_mode")
        utc(self.observed_at)  # Retained even under the explicitly non-PIT proxy.
        return max(clocks)


@dataclass(frozen=True)
class Session:
    day: str
    opened: datetime
    closed: datetime


class SessionGrid:
    def __init__(self, sessions: list[Session]):
        self.sessions = sorted(sessions, key=lambda x: x.closed)
        self.closes = [s.closed for s in self.sessions]
        self.by_day = {s.day: i for i, s in enumerate(self.sessions)}
        if len(self.by_day) != len(self.sessions):
            raise ValueError("duplicate_session")

    def origin(self, boundary: datetime, delay_seconds: int) -> Session | None:
        if delay_seconds < 0:
            raise ValueError("negative_information_delay")
        index = bisect_right(self.closes, boundary + timedelta(seconds=delay_seconds))
        return self.sessions[index] if index < len(self.sessions) else None

    def path(self, origin_day: str, horizon: int) -> list[Session]:
        index = self.by_day[origin_day]
        return self.sessions[index:index + horizon + 1]


def exchange_grid(exchange: str, start: str, end: str) -> SessionGrid:
    from ingestion.prices.yahoo_daily_v1 import ExchangeCalendarResolver
    if not exchange:
        raise ValueError("missing_exchange_calendar_identity")
    resolver = ExchangeCalendarResolver(exchange, start=start, end=end)
    current, last = date.fromisoformat(start), date.fromisoformat(end)
    sessions = []
    while current <= last:
        bounds = resolver.bounds(current.isoformat())
        if bounds is not None:
            sessions.append(Session(current.isoformat(), bounds.open_utc, bounds.close_utc))
        current += timedelta(days=1)
    return SessionGrid(sessions)


def ro_connect(path: Path, query_seconds: float = 30) -> sqlite3.Connection:
    conn = sqlite3.connect(path.resolve().as_uri() + "?mode=ro", uri=True, timeout=5)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA query_only=ON")
    deadline = [time.monotonic() + query_seconds]
    conn.set_trace_callback(lambda _: deadline.__setitem__(0, time.monotonic() + query_seconds))
    conn.set_progress_handler(lambda: int(time.monotonic() > deadline[0]), 10000)
    conn.execute("BEGIN")
    return conn


def file_state(path: Path) -> dict[str, Any]:
    out = {}
    for suffix in ("", "-wal", "-journal"):
        p = Path(str(path) + suffix)
        stat = p.stat() if p.exists() else None
        out[suffix or "main"] = None if stat is None else [stat.st_size, stat.st_mtime_ns]
    return out


def load_contract(path: Path) -> dict[str, Any]:
    cfg = json.loads(path.read_text(encoding="utf-8"))
    return validate_contract(cfg)


def validate_contract(cfg: dict[str, Any]) -> dict[str, Any]:
    expected = {
        "dataset_contract": CONTRACT,
        "event_feature_version": "event_state_v0031_deep",
        "normalization_version": "sec_event_normalizer_v0031_deep_raw_lineage",
        "market_feature_version": "market_daily_state_v003_core",
        "source_label_version": "market_daily_reaction_v003_core",
        "label_version": "event_distributional_close_aligned_v001",
        "legacy_label_version": "event_reaction_daily_v0031_deep",
        "projection_version": "event_arrival_set_v001",
        "clock_policy": "historical_public_proxy",
        "origin_policy": "first_exchange_close_strictly_after_information_boundary",
        "strict_pit": False, "training_authorized": False,
        "source_database": "data/database/market_data_v2.db",
        "market_database": "data/processed/market_daily_v003_core.db",
        "market_specification": "config/market_brain_distributional_v0081.json",
    }
    if any(cfg.get(k) != v for k, v in expected.items()):
        raise ValueError("unsupported_dataset_contract")
    if not (cfg["research_start_day"] <= cfg["research_end_day"] <= FROZEN_END):
        raise ValueError("research_window_overlaps_prospective_data")
    if cfg["horizons_sessions"] != [1, 3, 5, 10]:
        raise ValueError("explicit_four_horizon_contract_required")
    delays = cfg["delay_sensitivity_seconds"]
    if not delays or delays[0] != 0 or len(set(delays)) != len(delays) or any(
        type(d) is not int or d < 0 for d in delays
    ):
        raise ValueError("invalid_delay_scenarios")
    return cfg


STATE_SQL = """
SELECT s.*, a.ticker, a.exchange,
       o.available_at observation_available_at, o.evidence_cutoff_at,
       o.event_id observation_event_id, o.normalization_run_id observation_run_id,
       v.occurred_at, v.scheduled_for, v.event_time_status,
       v.event_id version_event_id, v.event_type version_event_type,
       i.identity_key, i.identity_method, n.normalization_version
FROM normalized_event_state_snapshots s
JOIN assets a ON a.asset_id=s.asset_id
JOIN normalized_event_observations o ON o.event_observation_id=s.event_observation_id
JOIN normalized_event_versions v ON v.event_version_id=o.event_version_id
JOIN normalized_event_identities i ON i.event_id=s.event_id
JOIN event_normalization_runs n ON n.normalization_run_id=s.normalization_run_id
WHERE s.feature_version=?
ORDER BY julianday(s.state_time),s.event_state_id
"""

EVIDENCE_SQL = """
SELECT m.*, l.available_at link_available_at, l.linking_method,
       r.raw_document_id,r.source_id,r.published_at,r.retrieved_at,
       r.available_at raw_available_at,r.modified_at,r.raw_sha256,
       COALESCE(es.semantic_type,'unknown') semantic_type,
       es.available_at semantic_available_at,
       p.evidence_available_at matched_available_at,
       p.clustering_run_id matched_run_id,p.cluster_id matched_cluster_id,
       fv.version_status,sf.accession_number,sf.acceptance_datetime
FROM event_cluster_event_links l
JOIN event_cluster_memberships m
  ON m.clustering_run_id=l.clustering_run_id AND m.cluster_id=l.cluster_id
LEFT JOIN event_cluster_raw_membership_refs rr ON rr.membership_id=m.membership_id
LEFT JOIN raw_source_documents r ON r.raw_document_id=rr.raw_document_id
LEFT JOIN event_evidence_semantics es
  ON es.normalization_run_id=l.normalization_run_id AND es.membership_id=m.membership_id
LEFT JOIN event_cluster_memberships p ON p.membership_id=m.matched_membership_id
LEFT JOIN sec_filing_file_versions fv ON fv.raw_document_id=r.raw_document_id
LEFT JOIN sec_filings sf ON sf.raw_document_id=fv.filing_raw_document_id
WHERE l.normalization_run_id=? AND l.event_observation_id=?
ORDER BY m.decision_order,m.membership_id
"""


def prepare_state(state: dict, evidence: list[dict], cfg: dict) -> dict:
    """Validate the historical snapshot, never enrich it with a later member."""
    t = utc(state["state_time"])
    boundary = max(t, utc(state["available_at"]), utc(state["observation_available_at"]))
    if state["normalization_version"] != cfg["normalization_version"]:
        raise ValueError("normalization_version_mismatch")
    if (state["observation_event_id"] != state["event_id"]
        or state["version_event_id"] != state["event_id"]
        or state["observation_run_id"] != state["normalization_run_id"]
        or state["event_type"] != state["version_event_type"]):
        raise ValueError("state_identity_or_version_mismatch")
    if utc(state["observation_available_at"]) > t:
        raise ValueError("future_event_observation_in_state")
    identity = re.fullmatch(r"sec:(\d{10}-\d{2}-\d{6}):.+", state["identity_key"])
    if identity is None:
        raise ValueError("unsupported_event_identity")
    accession = identity.group(1)
    included, seen_members = {}, {}
    future_members = 0
    for row in evidence:
        member_time = utc(row["evidence_available_at"])
        if member_time > t:
            future_members += 1
            continue
        if row["membership_id"] in seen_members:
            if seen_members[row["membership_id"]] != row:
                raise ValueError("ambiguous_membership_lineage")
            continue
        seen_members[row["membership_id"]] = row
        if row["evidence_type"] != "raw_source_document" or not row["raw_document_id"]:
            raise ValueError("unsupported_or_missing_raw_lineage")
        if row["accession_number"] != accession:
            raise ValueError("cross_accession_evidence_requires_review")
        if row["version_status"] not in {"canonical", "identical_rerun"}:
            raise ValueError("revised_bytes_without_historical_version_clock")
        if row["availability_basis"] != "sec_acceptance_datetime" or (
            row["linking_method"] != "sec_accession_provenance"
        ):
            raise ValueError("unsupported_historical_evidence_contract")
        if row["match_method"] not in {"anchor", "sec_accession_provenance"}:
            raise ValueError("text_cluster_requires_separate_causal_review")
        if utc(row["link_available_at"]) > t:
            raise ValueError("future_cluster_link_in_state")
        if row["matched_membership_id"] and (
            not row["matched_available_at"]
            or utc(row["matched_available_at"]) > member_time
            or row["matched_run_id"] != row["clustering_run_id"]
            or row["matched_cluster_id"] != row["cluster_id"]
        ):
            raise ValueError("future_or_foreign_cluster_anchor")
        if row["semantic_available_at"] and utc(row["semantic_available_at"]) > t:
            raise ValueError("future_semantics_in_state")
        if row["semantic_type"] not in SEMANTICS:
            raise ValueError("unknown_semantic_taxonomy")
        clock = EvidenceClock(row["evidence_available_at"], row["published_at"],
                              row["retrieved_at"], row["link_available_at"], row["modified_at"])
        ready = max(clock.boundary(cfg["clock_policy"]), utc(row["raw_available_at"]),
                    utc(row["acceptance_datetime"]))
        if utc(row["retrieved_at"]) < ready:
            raise ValueError("retrieval_precedes_claimed_content_availability")
        boundary = max(boundary, ready)
        key = row["raw_sha256"]
        if not key or not re.fullmatch(r"[0-9a-f]{64}", key):
            raise ValueError("invalid_content_hash")
        item = dict(row, effective_available_at=iso(ready))
        if key in included and included[key]["semantic_type"] != item["semantic_type"]:
            raise ValueError("conflicting_duplicate_semantics")
        if key not in included or utc(item["effective_available_at"]) < utc(included[key]["effective_available_at"]):
            included[key] = item
    if not seen_members:
        raise ValueError("empty_asof_evidence")
    if len(seen_members) != int(state["evidence_count"]):
        raise ValueError("persisted_evidence_count_mismatch")
    semantic_counts = Counter(r["semantic_type"] for r in seen_members.values())
    for semantic in SEMANTICS:
        if int(state[f"semantic_{semantic}_count"]) != semantic_counts[semantic]:
            raise ValueError("persisted_semantic_count_mismatch")
    first = min(utc(r["evidence_available_at"]) for r in seen_members.values())
    if utc(state["first_evidence_at"]) != first:
        raise ValueError("first_evidence_clock_mismatch")
    return {
        "state": state, "boundary": iso(boundary), "accession": accession,
        "evidence": sorted(included.values(), key=lambda r: r["raw_sha256"]),
        "memberships": sorted(seen_members.values(), key=lambda r: r["membership_id"]),
        "earliest_known_evidence_at": iso(first),
        "first_public_status": "UNKNOWN_EARLIER_DISCLOSURE_POSSIBLE",
        "future_members_not_used": future_members,
        "boundary_shift_seconds": (boundary - t).total_seconds(),
    }


SCHEMA = """
PRAGMA foreign_keys=ON;
CREATE TABLE metadata(key TEXT PRIMARY KEY,value_json TEXT NOT NULL);
CREATE TABLE state_audit(
 event_state_id TEXT PRIMARY KEY,event_id TEXT NOT NULL,asset_id INTEGER NOT NULL,
 status TEXT NOT NULL,reason TEXT,payload_json TEXT NOT NULL);
CREATE TABLE alignment_audit(
 event_state_id TEXT NOT NULL,delay_seconds INTEGER NOT NULL,status TEXT NOT NULL,
 origin_day TEXT,reason TEXT,PRIMARY KEY(event_state_id,delay_seconds));
CREATE TABLE samples(
 sample_id TEXT PRIMARY KEY,asset_id INTEGER NOT NULL,ticker TEXT NOT NULL,
 origin_day TEXT NOT NULL,origin_time TEXT NOT NULL,delay_seconds INTEGER NOT NULL,
 market_state_id TEXT NOT NULL,information_cutoff TEXT NOT NULL,
 strict_pit INTEGER NOT NULL CHECK(strict_pit=0),
 market_features_json TEXT NOT NULL,event_features_json TEXT NOT NULL,
 UNIQUE(asset_id,origin_day,delay_seconds));
CREATE TABLE sample_events(
 sample_id TEXT NOT NULL REFERENCES samples(sample_id),
 event_id TEXT NOT NULL,event_state_id TEXT NOT NULL REFERENCES state_audit(event_state_id),
 accession TEXT NOT NULL,boundary TEXT NOT NULL,
 PRIMARY KEY(sample_id,event_id));
CREATE TABLE sample_groups(
 sample_id TEXT NOT NULL REFERENCES samples(sample_id),group_kind TEXT NOT NULL,
 group_id TEXT NOT NULL,PRIMARY KEY(sample_id,group_kind,group_id));
CREATE TABLE outcomes(
 sample_id TEXT NOT NULL REFERENCES samples(sample_id),horizon_sessions INTEGER NOT NULL,
 status TEXT NOT NULL,reason TEXT,source_label_id TEXT,source_label_version TEXT,
 label_version TEXT NOT NULL,origin_day TEXT NOT NULL,target_day TEXT,
 target_time TEXT,return_pct REAL,mfe_pct REAL,mae_pct REAL,realized_path_vol_pct REAL,
 trace_json TEXT NOT NULL,PRIMARY KEY(sample_id,horizon_sessions));
CREATE INDEX outcomes_status ON outcomes(horizon_sessions,status);
CREATE TABLE legacy_label_audit(
 event_state_id TEXT NOT NULL,horizon_sessions INTEGER NOT NULL,
 legacy_label_id TEXT,legacy_status TEXT,legacy_origin_day TEXT,
 PRIMARY KEY(event_state_id,horizon_sessions));
"""


def load_prices(conn: sqlite3.Connection, asset_id: int, cfg: dict) -> dict[str, dict]:
    rows = conn.execute("""
    WITH ranked AS (
      SELECT g.*,ROW_NUMBER() OVER(PARTITION BY asset_id,trading_day
        ORDER BY observation_sequence DESC,julianday(observed_at) DESC,
                 price_observation_id DESC) rank
      FROM daily_price_quality_gated_observations_v001 g
      WHERE asset_id=? AND interval='1d' AND trading_day BETWEEN ? AND ?
        AND julianday(available_at)<=julianday(bar_end_utc)
    ) SELECT trading_day,bar_end_utc,open,high,low,close,
             price_observation_id,observed_at,available_at
      FROM ranked WHERE rank=1 ORDER BY trading_day
    """, (asset_id, cfg["research_start_day"], cfg["research_end_day"]))
    return {r["trading_day"]: dict(r) for r in rows}


def action_days(conn: sqlite3.Connection, asset_id: int) -> set[str]:
    rows = conn.execute("""
    WITH ranked AS (
      SELECT o.effective_trading_day,v.is_present,ROW_NUMBER() OVER(
        PARTITION BY o.asset_id,o.action_type,o.effective_trading_day
        ORDER BY o.observation_sequence DESC,julianday(o.observed_at) DESC,
                 o.action_observation_id DESC) rank
      FROM corporate_action_observations o JOIN corporate_action_versions v
      ON v.corporate_action_version_id=o.corporate_action_version_id
      WHERE o.asset_id=?
    ) SELECT effective_trading_day FROM ranked WHERE rank=1 AND is_present=1
    """, (asset_id,))
    return {r[0] for r in rows}


def validate_outcome(label: dict | None, sample: dict, horizon: int,
                     grid: SessionGrid, prices: dict, actions: set, cfg: dict) -> dict:
    result = dict(status="excluded", reason=None, source_label_id=None,
                  source_label_version=None, target_day=None, target_time=None,
                  trace={}, **dict.fromkeys(TARGETS))
    path = grid.path(sample["origin_day"], horizon)
    if len(path) != horizon + 1:
        return dict(result, reason="outside_research_window")
    result.update(target_day=path[-1].day, target_time=iso(path[-1].closed))
    if label is None:
        return dict(result, reason="missing_source_label")
    result.update(source_label_id=label["label_id"], source_label_version=label["label_version"])
    if label["label_version"] != cfg["source_label_version"] or (
        label["state_id"] != sample["market_state_id"]
        or label["asset_id"] != sample["asset_id"]
        or label["origin_trading_day"] != sample["origin_day"]
        or label["horizon_sessions"] != horizon
    ):
        return dict(result, status="invalid", reason="label_identity_or_version_mismatch")
    if any(s.day not in prices for s in path):
        return dict(result, reason="missing_exchange_session_price")
    bars = [prices[s.day] for s in path]
    result["trace"] = {"bars": bars, "corporate_action_days": sorted(
        d for d in actions if path[0].day < d <= path[-1].day)}
    if any(utc(b["bar_end_utc"]) != s.closed for b, s in zip(bars, path)):
        return dict(result, status="invalid", reason="price_session_clock_mismatch")
    if label["target_trading_day"] != path[-1].day:
        return dict(result, status="invalid", reason="label_session_horizon_mismatch")
    if result["trace"]["corporate_action_days"] or label["corporate_action_overlap"]:
        return dict(result, reason="corporate_action_overlap")
    if label["label_status"] != "usable":
        return dict(result, reason=f"source_label_{label['label_status']}")
    if any(not isinstance(b.get(k), (int, float)) or not math.isfinite(b[k]) or b[k] <= 0
           for b in bars for k in ("open", "high", "low", "close")):
        return dict(result, status="invalid", reason="invalid_ohlc")
    if any(b["low"] > min(b["open"], b["close"]) or b["high"] < max(b["open"], b["close"])
           for b in bars):
        return dict(result, status="invalid", reason="incoherent_ohlc")
    close = bars[0]["close"]
    steps = [100 * (b["close"] / a["close"] - 1) for a, b in zip(bars, bars[1:])]
    computed = dict(return_pct=100 * (bars[-1]["close"] / close - 1),
                    mfe_pct=100 * (max(b["high"] for b in bars[1:]) / close - 1),
                    mae_pct=100 * (min(b["low"] for b in bars[1:]) / close - 1),
                    realized_path_vol_pct=statistics.pstdev(steps))
    result["trace"]["source_values"] = {k: label[k] for k in TARGETS}
    if any(not isinstance(label[k], (float, int)) or not math.isfinite(label[k])
           or not math.isclose(label[k], computed[k], rel_tol=1e-7, abs_tol=1e-7)
           for k in TARGETS):
        return dict(result, status="invalid", reason="persisted_label_math_mismatch")
    return dict(result, status="usable", **computed)


def event_projection(prepared: list[dict], origin: datetime) -> dict:
    evidence = {}
    for p in prepared:
        for e in p["evidence"]:
            key = e["raw_sha256"]
            if key in evidence and evidence[key]["semantic_type"] != e["semantic_type"]:
                raise ValueError("cross_event_duplicate_semantic_conflict")
            evidence[key] = e
    semantics = Counter(e["semantic_type"] for e in evidence.values())
    boundaries = [utc(p["boundary"]) for p in prepared]
    first = min(utc(p["earliest_known_evidence_at"]) for p in prepared)
    return {
        "event_count": len(prepared), "unique_evidence_count": len(evidence),
        "distinct_event_type_count": len({p["state"]["event_type"] for p in prepared}),
        "seconds_since_latest_state": (origin - max(boundaries)).total_seconds(),
        "seconds_since_earliest_state": (origin - min(boundaries)).total_seconds(),
        "seconds_since_earliest_known_evidence": (origin - first).total_seconds(),
        **{f"semantic_{s}_count": semantics[s] for s in SEMANTICS},
        "event_types": sorted(p["state"]["event_type"] for p in prepared),
    }


def make_sample(asset_id: int, origin: Session, delay: int, market: dict,
                prepared: list[dict], market_features: list[str], cfg: dict,
                *, contract: str = CONTRACT) -> dict:
    if not market_features or not set(market_features).issubset(MARKET_ALLOWLIST):
        raise ValueError("forbidden_market_feature")
    if market["feature_version"] != cfg["market_feature_version"] or (
        market["asset_id"] != asset_id or market["trading_day"] != origin.day
        or utc(market["state_time"]) != origin.closed
    ):
        raise ValueError("market_identity_version_or_session_mismatch")
    features = {k: market[k] for k in market_features}
    if any(not isinstance(v, (float, int)) or not math.isfinite(v) for v in features.values()):
        raise ValueError("missing_or_nonfinite_market_features")
    cutoff = max(utc(p["boundary"]) + timedelta(seconds=delay) for p in prepared)
    if cutoff >= origin.closed:
        raise ValueError("future_information_at_origin")
    return dict(sample_id="eds_" + digest([contract, cfg, asset_id, origin.day, delay]),
                asset_id=asset_id, ticker=market["ticker"], origin_day=origin.day,
                origin_time=iso(origin.closed), delay_seconds=delay,
                market_state_id=market["state_id"], information_cutoff=iso(cutoff),
                strict_pit=0, market_features_json=canonical(features),
                event_features_json=canonical(event_projection(prepared, origin.closed)))


def insert_dict(conn: sqlite3.Connection, table: str, row: dict) -> None:
    # Table/column names are module constants, not external data.
    conn.execute(f"INSERT INTO {table}({','.join(row)}) VALUES({','.join('?' for _ in row)})",
                 list(row.values()))


@dataclass(frozen=True)
class DatasetPolicy:
    """Explicit version hooks; never monkeypatch module globals between runs.

    V001 defaults retain their historical semantics for reproduction/tests.
    New contracts must supply their own preparation and supplemental audit.
    """
    contract: str
    prepare: Callable
    evidence_sql: str
    extra_audit: Callable | None = None
    render: Callable | None = None
    code_paths: tuple[Path, ...] = ()


def build(cfg: dict, output: Path, source_path: Path, market_path: Path,
          market_features: list[str], max_states: int | None = None,
          query_seconds: float = 30, *, policy: DatasetPolicy | None = None) -> dict:
    policy = policy or DatasetPolicy(CONTRACT, prepare_state, EVIDENCE_SQL)
    if cfg["dataset_contract"] != policy.contract:
        raise ValueError("dataset_policy_contract_mismatch")
    if output.exists():
        manifest_path = output / "manifest.json"
        if not manifest_path.is_file():
            raise ValueError("incomplete_output_exists_use_a_new_run_id")
        previous = json.loads(manifest_path.read_text())
        current_inputs = {str(p.resolve()): file_state(p) for p in (source_path, market_path)}
        same = (previous["contract"] == cfg and previous["source_files_after"] == current_inputs
                and previous["market_feature_allowlist"] == market_features
                and previous["max_states_requested"] == max_states
                and all(file_digest(ROOT / p) == sha for p, sha in previous["code_sha256"].items())
                and file_digest(output / "dataset.sqlite") == previous["dataset_sha256"])
        if not same:
            raise ValueError("output_contract_or_inputs_changed_use_a_new_run_id")
        return audit(output / "dataset.sqlite", policy=policy)
    if source_path.resolve() == market_path.resolve():
        raise ValueError("source_and_market_databases_must_differ")
    output.mkdir(parents=True, exist_ok=False)
    db = output / "dataset.sqlite"
    before = {str(p.resolve()): file_state(p) for p in (source_path, market_path)}
    with closing(ro_connect(source_path, query_seconds)) as source, closing(
        ro_connect(market_path, query_seconds)
    ) as market, closing(sqlite3.connect(db)) as dest:
        dest.row_factory = sqlite3.Row
        dest.executescript(SCHEMA)
        source_contract = source.execute("""SELECT selection_point_in_time_verified,cutoff_column
            FROM daily_price_asof_configs WHERE asof_contract_version='daily_price_asof_v1'
            AND mode='historical_session_close_assumption'""").fetchall()
        if len(source_contract) != 1 or tuple(source_contract[0]) != (0, "available_at"):
            raise ValueError("source_price_asof_contract_mismatch")
        source_count = source.execute(
            "SELECT COUNT(*) FROM normalized_event_state_snapshots WHERE feature_version=?",
            (cfg["event_feature_version"],)).fetchone()[0]
        states = [dict(r) for r in source.execute(STATE_SQL, (cfg["event_feature_version"],))]
        if len(states) != source_count:
            raise ValueError("orphan_state_hidden_by_source_join")
        # Chronologically spread smoke sample, selected without labels or returns.
        if max_states is not None and len(states) > max_states:
            indices = [i * (len(states) - 1) // max(max_states - 1, 1) for i in range(max_states)]
            states = [states[i] for i in indices]
        grids, evidence_cache, exchange_cache = {}, {}, {}
        pending = defaultdict(list)
        for i, state in enumerate(states):
            sid = state["event_state_id"]
            status, reason, payload = "eligible", None, {"state": state}
            try:
                aid = state["asset_id"]
                if aid not in exchange_cache:
                    exchanges = {r[0] for r in source.execute("""SELECT DISTINCT exchange
                        FROM daily_price_quality_gated_observations_v001 WHERE asset_id=?
                        AND interval='1d' AND trading_day BETWEEN ? AND ?""",
                        (aid, cfg["research_start_day"], cfg["research_end_day"]))}
                    exchange_cache[aid] = next(iter(exchanges)) if len(exchanges) == 1 else None
                verified_exchange = exchange_cache[aid]
                if not verified_exchange:
                    raise ValueError("missing_or_changing_historical_exchange_requires_review")
                state["catalog_exchange"] = state["exchange"]
                state["exchange"] = verified_exchange
                state["exchange_basis"] = "unambiguous_quality_price_exchange_in_research_window"
                key = (state["normalization_run_id"], state["event_observation_id"])
                if key not in evidence_cache:
                    evidence_cache[key] = [dict(r) for r in source.execute(policy.evidence_sql, key)]
                payload["candidate_memberships_for_review_only"] = evidence_cache[key]
                payload = policy.prepare(state, evidence_cache[key], cfg)
                exchange = state["exchange"]
                if exchange not in grids:
                    grids[exchange] = exchange_grid(exchange, cfg["research_start_day"], cfg["research_end_day"])
                grid = grids[exchange]
                for delay in cfg["delay_sensitivity_seconds"]:
                    boundary = utc(payload["boundary"])
                    origin = grid.origin(boundary, delay)
                    alignment_status = "pending" if origin else "excluded"
                    alignment_reason = None if origin else "no_origin_in_research_window"
                    if boundary.date().isoformat() < cfg["research_start_day"]:
                        alignment_status, alignment_reason = "excluded", "before_research_window"
                    dest.execute("INSERT INTO alignment_audit VALUES(?,?,?,?,?)",
                                 (sid, delay, alignment_status, origin.day if origin else None, alignment_reason))
                    if alignment_status == "pending":
                        pending[(state["asset_id"], exchange, origin.day, delay)].append(payload)
            except (ValueError, TypeError, KeyError) as exc:
                status, reason = "excluded", str(exc)
            dest.execute("INSERT INTO state_audit VALUES(?,?,?,?,?,?)",
                         (sid, state["event_id"], state["asset_id"], status, reason, canonical(payload)))
            # Diagnostic metadata only; never reuse old event-reaction return values.
            for old in source.execute("""SELECT reaction_label_id,horizon_sessions,label_status,
                   origin_trading_day FROM normalized_event_reaction_labels
                   WHERE event_state_id=? AND label_version=?""", (sid, cfg["legacy_label_version"])):
                dest.execute("INSERT INTO legacy_label_audit VALUES(?,?,?,?,?)",
                             (sid, old["horizon_sessions"], old["reaction_label_id"], old["label_status"], old["origin_trading_day"]))
            if (i + 1) % 100 == 0:
                print(canonical({"stage": "state_lineage", "completed": i + 1, "total": len(states)}), flush=True)

        price_cache, action_cache = {}, {}
        for sample_index, ((aid, exchange, day, delay), candidates) in enumerate(sorted(pending.items())):
            if sample_index and sample_index % 100 == 0:
                print(canonical({"stage": "align_and_verify_outcomes", "completed": sample_index,
                                 "total": len(pending)}), flush=True)
            grid = grids[exchange]
            origin = grid.sessions[grid.by_day[day]]
            latest = {}
            for p in sorted(candidates, key=lambda p: (utc(p["state"]["state_time"]), p["state"]["event_state_id"])):
                event_id = p["state"]["event_id"]
                if event_id in latest and utc(latest[event_id]["state"]["state_time"]) == utc(p["state"]["state_time"]):
                    raise ValueError("ambiguous_event_state_at_same_time")
                latest[event_id] = p
            chosen = list(latest.values())
            rows = market.execute("""SELECT * FROM market_daily_v003_states
                                      WHERE asset_id=? AND trading_day=?""", (aid, day)).fetchall()
            try:
                if len(rows) != 1:
                    raise ValueError("missing_or_ambiguous_exact_market_state")
                sample = make_sample(aid, origin, delay, dict(rows[0]), chosen, market_features, cfg,
                                     contract=policy.contract)
            except (ValueError, KeyError, TypeError) as exc:
                for p in candidates:
                    dest.execute("UPDATE alignment_audit SET status='excluded',reason=? WHERE event_state_id=? AND delay_seconds=?",
                                 (str(exc), p["state"]["event_state_id"], delay))
                continue
            insert_dict(dest, "samples", sample)
            selected_ids = {p["state"]["event_state_id"] for p in chosen}
            for p in candidates:
                state_id = p["state"]["event_state_id"]
                dest.execute("UPDATE alignment_audit SET status=? WHERE event_state_id=? AND delay_seconds=?",
                             ("selected" if state_id in selected_ids else "superseded_within_session", state_id, delay))
            for p in chosen:
                dest.execute("INSERT INTO sample_events VALUES(?,?,?,?,?)",
                             (sample["sample_id"], p["state"]["event_id"], p["state"]["event_state_id"], p["accession"], p["boundary"]))
                groups = [("event", p["state"]["event_id"]), ("filing", p["accession"])]
                groups.extend(("content", e["raw_sha256"]) for e in p["evidence"])
                dest.executemany("INSERT OR IGNORE INTO sample_groups VALUES(?,?,?)",
                                 [(sample["sample_id"], kind, value) for kind, value in groups])
            if aid not in price_cache:
                price_cache[aid] = load_prices(source, aid, cfg)
                action_cache[aid] = action_days(source, aid)
            for h in cfg["horizons_sessions"]:
                labels = market.execute("""SELECT * FROM market_daily_v003_labels
                    WHERE state_id=? AND horizon_sessions=?""", (sample["market_state_id"], h)).fetchall()
                if len(labels) > 1:
                    raise ValueError("duplicate_source_label")
                outcome = validate_outcome(dict(labels[0]) if labels else None, sample, h,
                                           grid, price_cache[aid], action_cache[aid], cfg)
                trace = outcome.pop("trace")
                insert_dict(dest, "outcomes", dict(sample_id=sample["sample_id"], horizon_sessions=h,
                    **outcome, origin_day=day, label_version=cfg["label_version"], trace_json=canonical(diagnostic_tree(trace))))
        after = {str(p.resolve()): file_state(p) for p in (source_path, market_path)}
        manifest = {
            "contract": cfg, "contract_sha256": digest(cfg), "source_state_count": source_count,
            "sampled_state_count": len(states), "partial_run": max_states is not None,
            "max_states_requested": max_states,
            "market_feature_allowlist": market_features,
            "event_feature_allowlist": [*EVENT_NUMERIC, "event_types"],
            "source_files_before": before, "source_files_after": after,
            "source_files_stable": before == after,
            "source_scope": "selected version, rows and historical prices; not a whole-database content hash",
            "source_state_rows_sha256": digest(states),
            "code_sha256": {str(p.relative_to(ROOT)): file_digest(p) for p in (
                Path(__file__), ROOT / "features/events/event_state_v003_deep.py",
                ROOT / cfg["market_specification"],
                ROOT / "features/market/daily_v003_core.py", ROOT / "ingestion/prices/yahoo_daily_v1.py",
                *policy.code_paths)},
            "python_version": __import__("sys").version,
            "exchange_calendars_version": importlib.metadata.version("exchange-calendars"),
            "created_at": iso(datetime.now(timezone.utc)),
            "training_status": "BLOCKED_PENDING_FULL_AUDIT_REVIEW_AND_EXPERIMENT_PREREGISTRATION",
            "model_version": "NONE_DATA_PREPARATION_ONLY", "folds": "NOT_SELECTED_NO_TRAINING",
            "seed": "NOT_APPLICABLE_DETERMINISTIC", "bootstrap_unit": "NOT_RUN",
        }
        dest.execute("INSERT INTO metadata VALUES('manifest',?)", (canonical(manifest),))
        dest.commit()
    report = audit(db, policy=policy)
    manifest["dataset_sha256"] = file_digest(db)
    (output / "manifest.json").write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "audit.json").write_text(json.dumps(report, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    (output / "AUDIT.md").write_text((policy.render or render_report)(report), encoding="utf-8")
    return report


def audit(db: Path, *, policy: DatasetPolicy | None = None) -> dict:
    policy = policy or DatasetPolicy(CONTRACT, prepare_state, EVIDENCE_SQL)
    failures, review = [], ["HISTORICAL_RECONSTRUCTION_NOT_STRICT_PIT", "FIRST_PUBLIC_DISCLOSURE_NOT_ESTABLISHED",
                            "ARRIVAL_DAY_SAMPLE_NOT_ALL_ASSET_DAYS", "TRAINING_NOT_PREREGISTERED"]
    with closing(ro_connect(db)) as conn:
        manifest = json.loads(conn.execute("SELECT value_json FROM metadata WHERE key='manifest'").fetchone()[0])
        cfg = manifest["contract"]
        if digest(cfg) != manifest["contract_sha256"] or cfg["dataset_contract"] != policy.contract:
            failures.append("MANIFEST_CONTRACT_MISMATCH")
        if not manifest["source_files_stable"]:
            failures.append("INPUTS_CHANGED_DURING_BUILD")
        if conn.execute("PRAGMA foreign_key_check").fetchall():
            failures.append("BROKEN_OUTPUT_FOREIGN_KEYS")
        states = [dict(r) for r in conn.execute("SELECT * FROM state_audit")]
        samples = [dict(r) for r in conn.execute("SELECT * FROM samples")]
        payloads = {r["event_state_id"]: json.loads(r["payload_json"]) for r in states}
        grids = {}
        if not samples:
            failures.append("EMPTY_SAMPLE_SET")
        if conn.execute("SELECT COUNT(*) FROM alignment_audit WHERE status='pending'").fetchone()[0]:
            failures.append("UNFINISHED_ALIGNMENT")
        if len(states) != manifest["sampled_state_count"]:
            failures.append("PERSISTED_STATE_COUNT_MISMATCH")
        for row in states:
            if row["status"] != "eligible":
                continue
            p = payloads[row["event_state_id"]]
            try:
                reconstructed = policy.prepare(p["state"], p["memberships"], cfg)
                for key in ("boundary", "evidence", "accession", "earliest_known_evidence_at"):
                    if reconstructed[key] != p[key]:
                        failures.append("STATE_PROVENANCE_REPLAY_MISMATCH")
            except (ValueError, KeyError, TypeError):
                failures.append("INVALID_PERSISTED_STATE_PROVENANCE")
        for sample in samples:
            sid = sample["sample_id"]
            if sid != "eds_" + digest([policy.contract, cfg, sample["asset_id"], sample["origin_day"], sample["delay_seconds"]]):
                failures.append("SAMPLE_ID_CONTRACT_MISMATCH")
            if sample["strict_pit"] != 0 or sample["origin_day"] > cfg["research_end_day"]:
                failures.append("PIT_OR_RESEARCH_WINDOW_VIOLATION")
            if utc(sample["information_cutoff"]) >= utc(sample["origin_time"]):
                failures.append("FUTURE_INFORMATION")
            m = json.loads(sample["market_features_json"])
            e = json.loads(sample["event_features_json"])
            if set(m) != set(manifest["market_feature_allowlist"]) or set(e) != set(manifest["event_feature_allowlist"]):
                failures.append("FEATURE_ALLOWLIST_VIOLATION")
            if not set(m).issubset(MARKET_ALLOWLIST) or set(e) != {*EVENT_NUMERIC, "event_types"}:
                failures.append("UNAUTHORIZED_FEATURE_FIELD")
            if any(not isinstance(v, (float, int)) or not math.isfinite(v) for v in m.values()):
                failures.append("NONFINITE_MARKET_FEATURE")
            links = conn.execute("SELECT * FROM sample_events WHERE sample_id=?", (sid,)).fetchall()
            if not links:
                failures.append("SAMPLE_WITHOUT_EVENT")
                continue
            ps = [payloads[r["event_state_id"]] for r in links]
            expected_groups = set()
            for p in ps:
                expected_groups.update({("event", p["state"]["event_id"]), ("filing", p["accession"])})
                expected_groups.update(("content", e["raw_sha256"]) for e in p["evidence"])
            persisted_groups = {(r[0], r[1]) for r in conn.execute(
                "SELECT group_kind,group_id FROM sample_groups WHERE sample_id=?", (sid,))}
            if expected_groups != persisted_groups:
                failures.append("DEPENDENCE_GROUP_LINEAGE_MISMATCH")
            if event_projection(ps, utc(sample["origin_time"])) != e:
                failures.append("EVENT_PROJECTION_MISMATCH")
            for p in ps:
                exchange = p["state"]["exchange"]
                if exchange not in grids:
                    grids[exchange] = exchange_grid(exchange, cfg["research_start_day"], cfg["research_end_day"])
                expected = grids[exchange].origin(utc(p["boundary"]), sample["delay_seconds"])
                if expected is None or expected.day != sample["origin_day"] or iso(expected.closed) != sample["origin_time"]:
                    failures.append("NOT_FIRST_ELIGIBLE_SESSION_CLOSE")
                if p["state"]["asset_id"] != sample["asset_id"]:
                    failures.append("CROSS_ASSET_EVENT_JOIN")
                selected = conn.execute("SELECT status,origin_day FROM alignment_audit WHERE event_state_id=? AND delay_seconds=?",
                    (p["state"]["event_state_id"], sample["delay_seconds"])).fetchone()
                if selected is None or tuple(selected) != ("selected", sample["origin_day"]):
                    failures.append("ALIGNMENT_LINEAGE_MISMATCH")
                if utc(p["boundary"]) + timedelta(seconds=sample["delay_seconds"]) >= utc(sample["origin_time"]):
                    failures.append("LATE_EVIDENCE_IN_FEATURES")
            outcomes = conn.execute("SELECT * FROM outcomes WHERE sample_id=?", (sid,)).fetchall()
            if {o["horizon_sessions"] for o in outcomes} != set(cfg["horizons_sessions"]):
                failures.append("MISSING_OUTCOME_HORIZON")
            for outcome in outcomes:
                if outcome["label_version"] != cfg["label_version"]:
                    failures.append("OUTPUT_LABEL_VERSION_MISMATCH")
                if outcome["status"] == "invalid":
                    failures.append(outcome["reason"])
                if outcome["status"] != "usable":
                    if any(outcome[k] is not None for k in TARGETS):
                        failures.append("EXCLUDED_ROW_HAS_USABLE_TARGETS")
                    continue
                if not (sample["origin_day"] < outcome["target_day"] <= cfg["research_end_day"]):
                    failures.append("OUTCOME_WINDOW_VIOLATION")
                if any(outcome[k] is None or not math.isfinite(outcome[k]) for k in TARGETS):
                    failures.append("NONFINITE_OUTCOME")
                    continue
                trace = json.loads(outcome["trace_json"])
                source_label = dict(state_id=sample["market_state_id"], asset_id=sample["asset_id"],
                    origin_trading_day=sample["origin_day"], target_trading_day=outcome["target_day"],
                    label_id=outcome["source_label_id"], label_version=outcome["source_label_version"],
                    label_status="usable", corporate_action_overlap=0, horizon_sessions=outcome["horizon_sessions"],
                    **{k: outcome[k] for k in TARGETS})
                rebuilt = validate_outcome(source_label, sample, outcome["horizon_sessions"], grids[exchange],
                    {b["trading_day"]: b for b in trace.get("bars", [])}, set(trace.get("corporate_action_days", [])), cfg)
                if rebuilt["status"] != "usable" or rebuilt["target_time"] != outcome["target_time"]:
                    failures.append("OUTCOME_REPLAY_MISMATCH")
        state_exclusions = dict(Counter(r["reason"] for r in states if r["status"] != "eligible"))
        alignment = [dict(r) for r in conn.execute("SELECT delay_seconds,status,reason,COUNT(*) rows FROM alignment_audit GROUP BY 1,2,3")]
        counts = [dict(r) for r in conn.execute("""SELECT s.delay_seconds,o.horizon_sessions,o.status,o.reason,COUNT(*) rows
                     FROM outcomes o JOIN samples s USING(sample_id) GROUP BY 1,2,3,4 ORDER BY 1,2,3,4""")]
        assets = [dict(r) for r in conn.execute("SELECT delay_seconds,ticker,COUNT(*) samples FROM samples GROUP BY 1,2")]
        years = [dict(r) for r in conn.execute("SELECT delay_seconds,substr(origin_day,1,4) year,COUNT(*) samples FROM samples GROUP BY 1,2")]
        types = [dict(r) for r in conn.execute("""SELECT s.delay_seconds,json_extract(a.payload_json,'$.state.event_type') event_type,
                    COUNT(*) event_links FROM sample_events se JOIN samples s USING(sample_id)
                    JOIN state_audit a USING(event_state_id) GROUP BY 1,2""")]
        legacy_earlier = conn.execute("""SELECT COUNT(*) FROM sample_events se JOIN samples s USING(sample_id)
            JOIN legacy_label_audit l USING(event_state_id) WHERE s.delay_seconds=0 AND l.legacy_origin_day<s.origin_day""").fetchone()[0]
        for delay in cfg["delay_sensitivity_seconds"]:
            n = sum(1 for s in samples if s["delay_seconds"] == delay)
            if n < cfg["minimum_samples_per_scenario"]:
                review.append(f"SMALL_SAMPLE_SCENARIO_{delay}")
            for h in cfg["horizons_sessions"]:
                usable = sum(r["rows"] for r in counts if r["delay_seconds"] == delay and r["horizon_sessions"] == h and r["status"] == "usable")
                if usable == 0:
                    failures.append(f"EMPTY_USABLE_SCENARIO_{delay}_H{h}")
                elif usable / max(n, 1) < cfg["minimum_usable_fraction_per_horizon"]:
                    review.append(f"LOW_USABLE_FRACTION_SCENARIO_{delay}_H{h}")
        if manifest["partial_run"]:
            review.append("PARTIAL_SMOKE_RUN_NOT_A_CORPUS_AUDIT")
        if manifest["source_state_count"] < cfg["minimum_source_states"]:
            review.append("SOURCE_CORPUS_SMALLER_THAN_EXPECTED")
        if len({s["asset_id"] for s in samples}) < cfg["minimum_assets"]:
            review.append("FEWER_ASSETS_THAN_EXPECTED")
        if sum(state_exclusions.values()) / max(len(states), 1) > cfg["maximum_excluded_state_fraction"]:
            review.append("LARGE_STATE_EXCLUSION_FRACTION")
        extra = policy.extra_audit(conn, manifest) if policy.extra_audit else {}
        failures.extend(extra.pop("failures", []))
        review.extend(extra.pop("review", []))
    return dict(status="FAIL" if failures else "REVIEW", integrity_status="FAIL" if failures else "PASS",
                dataset_contract=policy.contract, failures=sorted(set(failures)), review=sorted(set(review)),
                source_states=manifest["source_state_count"], examined_states=len(states),
                unique_examined_events=len({s["event_id"] for s in states}), samples=len(samples),
                state_exclusions=state_exclusions, alignment_counts=alignment, outcome_counts=counts,
                per_asset=assets, per_year=years, per_event_type=types,
                legacy_label_links_with_earlier_origin=legacy_earlier,
                training_authorized=False, predictive_claim="NONE", strict_pit=False, **extra)


def render_report(report: dict) -> str:
    lines = ["# Distributional Event Dataset — auditoría", "",
             f"Estado: {report['status']}; integridad: {report['integrity_status']}.", "",
             f"Estados examinados: {report['examined_states']} / {report['source_states']}.",
             f"Eventos distintos examinados: {report['unique_examined_events']}.",
             f"Muestras: {report['samples']} (no sumar escenarios como observaciones independientes).", "",
             "Reconstrucción histórica, no strict PIT. Primera divulgación pública no demostrada.",
             "No se entrenó ningún modelo; V009 no se abrió ni modificó.", "",
             "## Resultados utilizables y exclusiones", "",
             "| Retraso adicional (s) | Horizonte | Estado | Motivo | Filas |",
             "|---:|---:|---|---|---:|"]
    for r in report["outcome_counts"]:
        lines.append(f"| {r['delay_seconds']} | {r['horizon_sessions']} | {r['status']} | {r['reason'] or '—'} | {r['rows']} |")
    lines.extend(["", "## Revisiones pendientes", ""])
    lines.extend(f"- {r}" for r in report["review"] + report["failures"])
    lines.extend(["", "Los relojes, fuentes, membresías y exclusiones por fila están en dataset.sqlite.",
                  "audit.json incluye cobertura por activo/año; manifest.json fija contratos y huellas.", ""])
    return "\n".join(lines)


def purged_partition(db: Path, *, delay_seconds: int, horizon: int,
                     test_start: str, test_end: str) -> dict[str, list[str]]:
    """Utility, NOT a selected experiment: purge time AND shared event/filing."""
    if test_start > test_end:
        raise ValueError("invalid_test_interval")
    with closing(ro_connect(db)) as conn:
        rows = [dict(r) for r in conn.execute("""SELECT s.sample_id,s.origin_day,o.target_day FROM samples s
            JOIN outcomes o USING(sample_id) WHERE s.delay_seconds=? AND o.horizon_sessions=? AND o.status='usable'""",
            (delay_seconds, horizon))]
        groups = defaultdict(set)
        for row in conn.execute("SELECT sample_id,group_kind,group_id FROM sample_groups"):
            groups[row["sample_id"]].add(row["group_kind"] + ":" + row["group_id"])
        test = [r for r in rows if test_start <= r["origin_day"] <= test_end]
        test_groups = set().union(*(groups[r["sample_id"]] for r in test)) if test else set()
        train, purged = [], []
        for row in rows:
            if row["origin_day"] >= test_start:
                continue
            target = train if row["target_day"] < test_start and not (groups[row["sample_id"]] & test_groups) else purged
            target.append(row["sample_id"])
        return {"train": sorted(train), "test": sorted(r["sample_id"] for r in test), "purged": sorted(purged)}


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", choices=("build", "audit"), required=True)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--max-states", type=int)
    ap.add_argument("--query-seconds", type=float, default=30)
    args = ap.parse_args()
    if args.stage == "build":
        ap.error("V001 superseded: HTTP Last-Modified is not event availability. "
                 "Use research.events.distributional_dataset_v002 with a new run-id.")
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", args.run_id):
        ap.error("run-id must be a simple name, not a path")
    if args.max_states is not None and args.max_states <= 0:
        ap.error("max-states must be positive")
    if args.query_seconds <= 0:
        ap.error("query-seconds must be positive")
    cfg = load_contract(args.config)
    output_root = (ROOT / cfg["output_root"]).resolve()
    allowed = (ROOT / "reports/distributional_event_dataset_v001").resolve()
    if output_root != allowed:
        ap.error("output must remain in the isolated event-dataset report root")
    output = output_root / args.run_id
    if output.is_symlink() or not output.resolve().is_relative_to(allowed):
        ap.error("unsafe output path")
    if args.stage == "build":
        from features.market.daily_v003_core import OWN_FEATURES
        spec = json.loads((ROOT / cfg["market_specification"]).read_text())
        if sorted(spec["frozen_own_features"]) != sorted(OWN_FEATURES):
            raise ValueError("market_feature_specification_changed")
        report = build(cfg, output, ROOT / cfg["source_database"], ROOT / cfg["market_database"],
                       spec["frozen_own_features"], args.max_states, args.query_seconds)
    else:
        manifest = json.loads((output / "manifest.json").read_text())
        if file_digest(output / "dataset.sqlite") != manifest["dataset_sha256"]:
            raise ValueError("output_database_hash_mismatch")
        report = audit(output / "dataset.sqlite")
        # Do not overwrite the historical report, but do not certify its known
        # invalid clock policy when someone invokes the legacy CLI today.
        report["status"] = report["integrity_status"] = "FAIL"
        report["failures"].append("V001_SUPERSEDED_HTTP_MODIFIED_IS_NOT_INFORMATION_ARRIVAL")
    # Keep detailed asset/year/type coverage in the local audit, not the terminal.
    print(json.dumps({k: v for k, v in report.items() if k not in
          {"per_asset", "per_year", "per_event_type", "alignment_counts", "outcome_counts"}},
          indent=2, ensure_ascii=False))
    raise SystemExit(1 if report["integrity_status"] == "FAIL" else 2)


if __name__ == "__main__":
    main()

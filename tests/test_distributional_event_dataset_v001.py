from __future__ import annotations

from copy import deepcopy
from datetime import timedelta
import json
from pathlib import Path
import sqlite3

import pytest

from research.events import distributional_dataset_v001 as d


@pytest.fixture
def cfg():
    return d.load_contract(d.DEFAULT_CONFIG)


@pytest.fixture
def state(cfg):
    t = "2020-01-02T14:00:00+00:00"
    return dict(event_state_id="s1", event_id="event1", asset_id=1, ticker="TEST",
                exchange="XNYS", state_time=t, available_at=t, first_evidence_at=t,
                observation_available_at=t, evidence_cutoff_at=t,
                normalization_run_id="run", observation_run_id="run",
                event_observation_id="obs1", observation_event_id="event1",
                version_event_id="event1", event_type="financial_results_disclosure",
                version_event_type="financial_results_disclosure", identity_method="sec_accession_item_v001",
                identity_key="sec:0000000001-20-000001:item:2.02", evidence_count=1,
                normalization_version=cfg["normalization_version"], feature_version=cfg["event_feature_version"],
                **{f"semantic_{s}_count": int(s == "official_statement") for s in d.SEMANTICS})


@pytest.fixture
def evidence(state):
    t = state["state_time"]
    return dict(membership_id="m1", evidence_available_at=t, evidence_type="raw_source_document",
                raw_document_id="raw1", raw_sha256="a" * 64, source_id="sec",
                accession_number="0000000001-20-000001", version_status="canonical",
                availability_basis="sec_acceptance_datetime", linking_method="sec_accession_provenance",
                match_method="anchor", link_available_at=t, matched_membership_id=None,
                matched_available_at=None, matched_run_id=None, matched_cluster_id=None,
                clustering_run_id="cluster_run", cluster_id="cluster1",
                semantic_available_at=t, semantic_type="official_statement", published_at=t,
                retrieved_at="2026-08-24T14:00:00+00:00", modified_at=None,
                raw_available_at=t, acceptance_datetime=t, normalization_run_id="run",
                event_observation_id="obs1")


@pytest.fixture
def grid():
    return d.exchange_grid("XNYS", "2020-01-02", "2020-02-03")


def test_live_late_delivery_is_not_publication_time():
    c = d.EvidenceClock("2020-01-02T14:00:00Z", "2020-01-02T13:00:00Z",
                        "2020-01-02T21:30:00Z", "2020-01-02T14:00:00Z")
    assert c.boundary("historical_public_proxy") == d.utc("2020-01-02T14:00:00Z")
    assert c.boundary("observed_capture") == d.utc("2020-01-02T21:30:00Z")


@pytest.mark.parametrize("value", ["2020-01-02", "2020-01-02T14:00:00", "nonsense", ""])
def test_uncertain_clock_not_silently_midnight(value):
    with pytest.raises(ValueError):
        d.utc(value)


def test_timezone_offsets_are_compared_as_instants():
    assert d.utc("2020-01-02T18:00:00-03:00") == d.utc("2020-01-02T21:00:00Z")


def test_close_equality_and_after_close_wait_for_next_session(grid):
    assert grid.origin(d.utc("2020-01-02T20:59:59Z"), 0).day == "2020-01-02"
    assert grid.origin(d.utc("2020-01-02T21:00:00Z"), 0).day == "2020-01-03"
    assert grid.origin(d.utc("2020-01-03T21:01:00Z"), 0).day == "2020-01-06"


def test_delay_is_sensitivity_not_backdated_entry(grid):
    assert grid.origin(d.utc("2020-01-02T20:30:00Z"), 3600).day == "2020-01-03"
    with pytest.raises(ValueError):
        grid.origin(d.utc("2020-01-02T20:30:00Z"), -1)


def test_holiday_early_close_and_dst():
    thanksgiving = d.exchange_grid("XNYS", "2020-11-25", "2020-11-30")
    assert "2020-11-26" not in thanksgiving.by_day
    assert thanksgiving.origin(d.utc("2020-11-27T18:00:00Z"), 0).day == "2020-11-30"
    dst = d.exchange_grid("XNYS", "2020-03-06", "2020-03-09")
    assert [s.closed.hour for s in dst.sessions] == [21, 20]


def test_original_snapshot_not_enriched_by_later_confirmation(cfg, state, evidence):
    base = d.prepare_state(state, [evidence], cfg)
    later = dict(evidence, membership_id="later", raw_sha256="b" * 64,
                 evidence_available_at="2020-01-03T14:00:00Z", semantic_type="correction")
    result = d.prepare_state(state, [evidence, later], cfg)
    assert result["evidence"] == base["evidence"]
    assert result["boundary"] == base["boundary"]
    assert result["future_members_not_used"] == 1
    assert result["first_public_status"] == "UNKNOWN_EARLIER_DISCLOSURE_POSSIBLE"


def test_earlier_public_document_linked_later_cannot_rewrite_state(cfg, state, evidence):
    evidence.update(published_at="2019-12-31T14:00:00Z", link_available_at="2020-01-03T14:00:00Z")
    with pytest.raises(ValueError, match="future_cluster_link"):
        d.prepare_state(state, [evidence], cfg)


def test_modified_bytes_push_boundary_forward(cfg, state, evidence):
    evidence["modified_at"] = "2020-01-02T21:01:00Z"
    prepared = d.prepare_state(state, [evidence], cfg)
    assert prepared["boundary"] == "2020-01-02T21:01:00+00:00"
    assert prepared["state"]["state_time"] == state["state_time"]
    assert prepared["memberships"][0]["retrieved_at"].startswith("2026")


@pytest.mark.parametrize("field,value,reason", [
    ("version_status", "revision_observed", "revised_bytes"),
    ("raw_document_id", None, "missing_raw_lineage"),
    ("accession_number", "0000000002-20-000001", "cross_accession"),
    ("semantic_available_at", "2020-01-03T14:00:00Z", "future_semantics"),
    ("match_method", "near_duplicate", "text_cluster"),
    ("raw_sha256", "bad", "content_hash"),
    ("retrieved_at", "2019-01-01T00:00:00Z", "retrieval_precedes"),
])
def test_ambiguous_evidence_fails_closed(cfg, state, evidence, field, value, reason):
    evidence[field] = value
    with pytest.raises(ValueError, match=reason):
        d.prepare_state(state, [evidence], cfg)


def test_future_cluster_anchor_is_rejected(cfg, state, evidence):
    evidence.update(matched_membership_id="future", matched_available_at="2020-01-03T14:00:00Z",
                    matched_run_id="cluster_run", matched_cluster_id="cluster1")
    with pytest.raises(ValueError, match="future_or_foreign_cluster_anchor"):
        d.prepare_state(state, [evidence], cfg)


def test_persisted_counts_must_match_lineage(cfg, state, evidence):
    state["evidence_count"] = 2
    with pytest.raises(ValueError, match="persisted_evidence_count"):
        d.prepare_state(state, [evidence], cfg)


def test_duplicate_content_not_counted_twice(cfg, state, evidence):
    duplicate = dict(evidence, membership_id="m2", raw_document_id="raw2")
    state.update(evidence_count=2, semantic_official_statement_count=2)
    p = d.prepare_state(state, [evidence, duplicate], cfg)
    assert len(p["memberships"]) == 2
    projection = d.event_projection([p], d.utc("2020-01-02T21:00:00Z"))
    assert projection["unique_evidence_count"] == 1
    assert projection["semantic_official_statement_count"] == 1


def test_same_filing_multiple_events_share_one_evidence_count(cfg, state, evidence):
    p = d.prepare_state(state, [evidence], cfg)
    other = deepcopy(p)
    other["state"]["event_id"] = "event2"
    projection = d.event_projection([p, other], d.utc("2020-01-02T21:00:00Z"))
    assert projection["event_count"] == 2
    assert projection["unique_evidence_count"] == 1


def market_row(session, cfg):
    return dict(state_id="market_" + session.day, asset_id=1, ticker="TEST",
                trading_day=session.day, state_time=d.iso(session.closed),
                feature_version=cfg["market_feature_version"], asset_vol_63d_pct=1.0)


def prices_and_label(grid, cfg, horizon=1):
    bars = {}
    for i, session in enumerate(grid.sessions):
        value = 100.0 + i
        bars[session.day] = dict(trading_day=session.day, bar_end_utc=d.iso(session.closed),
                                open=value, close=value, high=value + 1, low=value - 1)
    origin = grid.sessions[0].day
    target = grid.sessions[horizon].day
    steps = [100 * ((101 + i) / (100 + i) - 1) for i in range(horizon)]
    label = dict(label_id="label", label_version=cfg["source_label_version"],
                 state_id="market_" + origin, asset_id=1, origin_trading_day=origin,
                 target_trading_day=target, horizon_sessions=horizon,
                 label_status="usable", corporate_action_overlap=0,
                 return_pct=float(horizon), mfe_pct=float(horizon + 1), mae_pct=0.0,
                 realized_path_vol_pct=d.statistics.pstdev(steps))
    sample = dict(market_state_id=label["state_id"], asset_id=1, origin_day=origin)
    return bars, label, sample


def test_label_starts_at_new_origin_not_old_pre_news_price(grid, cfg):
    bars, label, sample = prices_and_label(grid, cfg)
    result = d.validate_outcome(label, sample, 1, grid, bars, set(), cfg)
    assert result["status"] == "usable"
    assert result["return_pct"] == pytest.approx(1.0)
    assert result["realized_path_vol_pct"] == 0
    label["origin_trading_day"] = "2020-01-01"
    assert d.validate_outcome(label, sample, 1, grid, bars, set(), cfg)["status"] == "invalid"


@pytest.mark.parametrize("h", [1, 3, 5, 10])
def test_all_horizon_math_reproduced(grid, cfg, h):
    bars, label, sample = prices_and_label(grid, cfg, h)
    assert d.validate_outcome(label, sample, h, grid, bars, set(), cfg)["status"] == "usable"


def test_missing_session_is_not_silently_skipped(grid, cfg):
    bars, label, sample = prices_and_label(grid, cfg, 3)
    del bars[grid.sessions[1].day]
    result = d.validate_outcome(label, sample, 3, grid, bars, set(), cfg)
    assert result["reason"] == "missing_exchange_session_price"
    assert result["return_pct"] is None


def test_future_return_does_not_select_an_origin(grid, cfg, state, evidence):
    p = d.prepare_state(state, [evidence], cfg)
    origin = grid.origin(d.utc(p["boundary"]), 0)
    market = market_row(origin, cfg)
    sample = d.make_sample(1, origin, 0, market, [p], ["asset_vol_63d_pct"], cfg)
    market.update(return_pct=9999, reaction_start_at="2019-01-01", label_status="usable")
    assert d.make_sample(1, origin, 0, market, [p], ["asset_vol_63d_pct"], cfg) == sample


@pytest.mark.parametrize("field,value", [("return_pct", 50), ("label_version", "old"),
    ("asset_id", 2), ("target_trading_day", "2020-01-08"), ("return_pct", float("nan"))])
def test_wrong_persisted_label_is_not_accepted(grid, cfg, field, value):
    bars, label, sample = prices_and_label(grid, cfg)
    label[field] = value
    assert d.validate_outcome(label, sample, 1, grid, bars, set(), cfg)["status"] == "invalid"


def test_corporate_actions_are_outcome_exclusions_not_features(grid, cfg):
    bars, label, sample = prices_and_label(grid, cfg)
    result = d.validate_outcome(label, sample, 1, grid, bars, {label["target_trading_day"]}, cfg)
    assert result["reason"] == "corporate_action_overlap"
    assert all(result[k] is None for k in d.TARGETS)


def test_no_future_holdout_labels_read(grid, cfg):
    bars, label, sample = prices_and_label(grid, cfg)
    tiny = d.SessionGrid(grid.sessions[:1])
    assert d.validate_outcome(None, sample, 10, tiny, {}, set(), cfg)["reason"] == "outside_research_window"


def test_historical_contract_cannot_be_promoted_to_strict_pit(tmp_path, cfg):
    cfg["strict_pit"] = True
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    with pytest.raises(ValueError, match="unsupported_dataset_contract"):
        d.load_contract(path)


def test_research_cannot_extend_into_v009(tmp_path, cfg):
    cfg["research_end_day"] = "2026-08-28"
    path = tmp_path / "config.json"
    path.write_text(json.dumps(cfg))
    with pytest.raises(ValueError, match="prospective_data"):
        d.load_contract(path)


def test_read_only_missing_db_never_created(tmp_path):
    missing = tmp_path / "missing.sqlite"
    with pytest.raises(sqlite3.OperationalError):
        d.ro_connect(missing)
    assert not missing.exists()


def test_read_only_connection_rejects_writes(tmp_path):
    path = tmp_path / "source.sqlite"
    with sqlite3.connect(path) as c:
        c.execute("CREATE TABLE data(x)")
    conn = d.ro_connect(path)
    try:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute("INSERT INTO data VALUES(1)")
    finally:
        conn.close()


def test_outcome_column_cannot_be_requested_as_feature(grid, cfg, state, evidence):
    p = d.prepare_state(state, [evidence], cfg)
    origin = grid.origin(d.utc(p["boundary"]), 0)
    market = dict(market_row(origin, cfg), return_pct=999)
    with pytest.raises(ValueError, match="forbidden_market_feature"):
        d.make_sample(1, origin, 0, market, [p], ["return_pct"], cfg)


def create_rows(conn, name, rows):
    columns = list(rows[0])
    definitions = []
    for k in columns:
        values = [r[k] for r in rows if r[k] is not None]
        kind = "INTEGER" if values and type(values[0]) is int else "REAL" if values and type(values[0]) is float else "TEXT"
        definitions.append(f'"{k}" {kind}')
    conn.execute(f'CREATE TABLE "{name}"({",".join(definitions)})')
    marks = ",".join("?" for _ in columns)
    conn.executemany(f'INSERT INTO "{name}" VALUES({marks})', [[r[k] for k in columns] for r in rows])


@pytest.fixture
def built(tmp_path, monkeypatch, cfg, state, evidence, grid):
    """Small complete builder test; real adapter SQL is also exercised by local smoke."""
    source_path, market_path = tmp_path / "source.sqlite", tmp_path / "market.sqlite"
    s2 = dict(state, event_state_id="s2", event_id="event2", event_observation_id="obs2",
              observation_event_id="event2", version_event_id="event2",
              identity_key="sec:0000000001-20-000001:item:7.01")
    e2 = dict(evidence, event_observation_id="obs2")
    with sqlite3.connect(source_path) as conn:
        create_rows(conn, "daily_price_asof_configs", [dict(selection_point_in_time_verified=0,
            cutoff_column="available_at", asof_contract_version="daily_price_asof_v1",
            mode="historical_session_close_assumption")])
        create_rows(conn, "normalized_event_state_snapshots", [state, s2])
        create_rows(conn, "evidence", [evidence, e2])
        create_rows(conn, "daily_price_quality_gated_observations_v001", [dict(
            asset_id=1, exchange="XNYS", interval="1d", trading_day="2020-01-02")])
        create_rows(conn, "normalized_event_reaction_labels", [dict(event_state_id="s1",
            reaction_label_id="old", horizon_sessions=1, label_status="usable",
            origin_trading_day="2019-12-31", label_version=cfg["legacy_label_version"])])
    labels = [prices_and_label(grid, cfg, h)[1] for h in cfg["horizons_sessions"]]
    with sqlite3.connect(market_path) as conn:
        create_rows(conn, "market_daily_v003_states", [market_row(s, cfg) for s in grid.sessions])
        create_rows(conn, "market_daily_v003_labels", labels)
    monkeypatch.setattr(d, "STATE_SQL", "SELECT * FROM normalized_event_state_snapshots WHERE feature_version=? ORDER BY state_time,event_state_id")
    monkeypatch.setattr(d, "EVIDENCE_SQL", "SELECT * FROM evidence WHERE normalization_run_id=? AND event_observation_id=?")
    monkeypatch.setattr(d, "exchange_grid", lambda *args: grid)
    monkeypatch.setattr(d, "load_prices", lambda *args: prices_and_label(grid, cfg)[0])
    monkeypatch.setattr(d, "action_days", lambda *args: set())
    cfg = dict(cfg, delay_sensitivity_seconds=[0])
    before = (d.file_digest(source_path), d.file_digest(market_path))
    output = tmp_path / "result"
    report = d.build(cfg, output, source_path, market_path, ["asset_vol_63d_pct"])
    return dict(cfg=cfg, output=output, report=report, source=source_path, market=market_path,
                before=before, db=output / "dataset.sqlite")


def test_full_builder_persists_one_row_for_same_asset_close(built):
    assert built["report"]["integrity_status"] == "PASS"
    assert built["report"]["samples"] == 1
    with sqlite3.connect(built["db"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sample_events").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM outcomes WHERE status='usable'").fetchone()[0] == 4
        assert conn.execute("SELECT COUNT(*) FROM sample_groups WHERE group_kind='filing'").fetchone()[0] == 1
    assert built["before"] == (d.file_digest(built["source"]), d.file_digest(built["market"]))


def test_repeat_build_is_idempotent_and_does_not_overwrite(built):
    before = d.file_digest(built["db"])
    report = d.build(built["cfg"], built["output"], built["source"], built["market"], ["asset_vol_63d_pct"])
    assert report == built["report"]
    assert d.file_digest(built["db"]) == before


def test_changed_source_requires_new_run_without_overwriting(built):
    with sqlite3.connect(built["source"]) as conn:
        conn.execute("UPDATE normalized_event_state_snapshots SET ticker='CHANGED'")
    with pytest.raises(ValueError, match="inputs_changed"):
        d.build(built["cfg"], built["output"], built["source"], built["market"], ["asset_vol_63d_pct"])


@pytest.mark.parametrize("statement,reason", [
    ("UPDATE samples SET information_cutoff='2030-01-01T00:00:00Z'", "FUTURE_INFORMATION"),
    ("UPDATE outcomes SET return_pct=999 WHERE horizon_sessions=1", "OUTCOME_REPLAY_MISMATCH"),
    ("DELETE FROM outcomes WHERE horizon_sessions=1", "MISSING_OUTCOME_HORIZON"),
    ("DELETE FROM sample_groups WHERE group_kind='filing'", "DEPENDENCE_GROUP_LINEAGE_MISMATCH"),
    ("UPDATE alignment_audit SET status='excluded'", "ALIGNMENT_LINEAGE_MISMATCH"),
])
def test_persisted_audit_detects_corruption(built, statement, reason):
    with sqlite3.connect(built["db"]) as conn:
        conn.execute(statement)
    assert reason in d.audit(built["db"])["failures"]


def test_output_features_have_no_outcomes_or_retrospective_clocks(built):
    with sqlite3.connect(built["db"]) as conn:
        a, b = conn.execute("SELECT market_features_json,event_features_json FROM samples").fetchone()
    assert set(json.loads(a)).isdisjoint(d.TARGETS)
    assert set(json.loads(b)).isdisjoint({*d.TARGETS, "retrieved_at", "reaction_start_at", "strict_pit"})


def test_invalid_nonfinite_label_is_reportable_not_silently_usable(built, tmp_path):
    with sqlite3.connect(built["market"]) as conn:
        conn.execute("UPDATE market_daily_v003_labels SET return_pct=? WHERE horizon_sessions=1", (float("inf"),))
    report = d.build(built["cfg"], tmp_path / "nonfinite", built["source"], built["market"], ["asset_vol_63d_pct"])
    assert report["integrity_status"] == "FAIL"
    assert "persisted_label_math_mismatch" in report["failures"]


def test_empty_inputs_do_not_pass_as_scientific_progress(built, tmp_path):
    with sqlite3.connect(built["source"]) as conn:
        conn.execute("DELETE FROM normalized_event_state_snapshots")
    report = d.build(built["cfg"], tmp_path / "empty", built["source"], built["market"], ["asset_vol_63d_pct"])
    assert report["integrity_status"] == "FAIL"
    assert "EMPTY_SAMPLE_SET" in report["failures"]


def test_temporal_purge_also_removes_shared_filing_and_content(tmp_path):
    path = tmp_path / "split.sqlite"
    with sqlite3.connect(path) as conn:
        conn.executescript("""CREATE TABLE samples(sample_id,origin_day,delay_seconds);
            CREATE TABLE outcomes(sample_id,horizon_sessions,status,target_day);
            CREATE TABLE sample_groups(sample_id,group_kind,group_id);""")
        rows = [("clean", "2020-01-02", "2020-01-03"),
                ("overlap", "2020-01-07", "2020-01-10"),
                ("filing", "2020-01-03", "2020-01-06"),
                ("content", "2020-01-03", "2020-01-06"),
                ("test", "2020-01-10", "2020-01-13")]
        for sid, origin, end in rows:
            conn.execute("INSERT INTO samples VALUES(?,?,0)", (sid, origin))
            conn.execute("INSERT INTO outcomes VALUES(?,1,'usable',?)", (sid, end))
        conn.executemany("INSERT INTO sample_groups VALUES(?,?,?)", [
            ("test", "filing", "f1"), ("filing", "filing", "f1"),
            ("test", "content", "sha"), ("content", "content", "sha"), ("clean", "event", "different")])
    partition = d.purged_partition(path, delay_seconds=0, horizon=1,
                                   test_start="2020-01-10", test_end="2020-01-10")
    assert partition == {"train": ["clean"], "test": ["test"], "purged": ["content", "filing", "overlap"]}

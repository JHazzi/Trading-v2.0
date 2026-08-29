from __future__ import annotations

from copy import deepcopy
from email.utils import format_datetime
import json
import sqlite3

import pytest

from research.events import distributional_dataset_v001 as engine
from research.events import distributional_dataset_v002 as d
from tests.test_distributional_event_dataset_v001 import (
    state, grid, evidence as legacy_evidence, create_rows, market_row, prices_and_label,
)


@pytest.fixture
def cfg():
    return d.load_contract(d.DEFAULT_CONFIG)


def http_metadata(value=None, **overrides):
    metadata = dict(exact_response_entity_bytes=True, availability_source="sec_acceptance_datetime",
                    response_headers={} if value is None else {
                        "last-modified": format_datetime(engine.utc(value), usegmt=True)})
    metadata.update(overrides)
    return json.dumps(metadata)


@pytest.fixture
def evidence(legacy_evidence):
    return dict(legacy_evidence, source_id="sec_edgar",
                raw_metadata_json=http_metadata(),
                version_observed_at=legacy_evidence["retrieved_at"])


def test_server_maintenance_never_moves_a_historical_event(cfg, state, evidence, grid):
    maintenance = "2021-01-11T20:25:25Z"
    evidence.update(modified_at=maintenance, raw_metadata_json=http_metadata(maintenance))
    original = deepcopy(evidence)
    p = d.prepare_state(state, [evidence], cfg)
    assert p["boundary"] == state["state_time"]
    assert p["boundary_shift_seconds"] == 0
    assert grid.origin(engine.utc(p["boundary"]), 0).day == "2020-01-02"
    assert p["memberships"][0] == original
    assert p["evidence"][0]["modified_at"] == maintenance
    assert p["evidence"][0]["modified_at_basis"] == "http_last_modified_metadata_only"
    assert p["clock_diagnostic"]["counterfactual_v001_shift_seconds"] > 365 * 86400
    assert not p["historical_bytes_verified"]
    assert evidence == original


@pytest.mark.parametrize("field,value,reason", [
    ("source_id", "other_source", "unverified_sec_clock_source"),
    ("version_status", "revision_observed", "revised_bytes"),
    ("version_observed_at", "2020-01-02T14:00:00Z", "version_observation_precedes"),
    ("published_at", "2020-01-03T14:00:00Z", "proxy_clock_mismatch"),
    ("raw_available_at", "2020-01-03T14:00:00Z", "proxy_clock_mismatch"),
    ("modified_at", "2021-01-01T00:00:00Z", "unverified_http_modified_metadata"),
    ("raw_metadata_json", "{}", "unverified_raw_response_provenance"),
    ("raw_metadata_json", "bad-json", "Expecting value"),
    ("link_available_at", "2020-01-03T14:00:00Z", "future_cluster_link"),
    ("semantic_available_at", "2020-01-03T14:00:00Z", "future_semantics"),
    ("retrieved_at", "2019-12-31T00:00:00Z", "retrieval_precedes"),
    ("match_method", "exact_text", "text_cluster"),
    ("accession_number", "0000000002-20-000001", "cross_accession"),
])
def test_unproven_clocks_and_lineage_fail_closed(cfg, state, evidence, field, value, reason):
    evidence[field] = value
    with pytest.raises(ValueError, match=reason):
        d.prepare_state(state, [evidence], cfg)


@pytest.mark.parametrize("metadata,reason", [
    (dict(availability_source="retrieved_at_revision"), "revision_or_unknown"),
    (dict(availability_source="unknown"), "revision_or_unknown"),
    (dict(exact_response_entity_bytes=False), "raw_response"),
    (dict(response_headers=None), "missing_http_header"),
    (dict(response_headers={"Last-Modified": "bad-date"}), "Invalid date"),
    (dict(response_headers={"last-modified": "Thu, 02 Jan 2020 14:00:00 GMT"}), "header_mismatch"),
    (dict(response_headers={"last-modified": "a", "Last-Modified": "b"}), "ambiguous_http"),
])
def test_header_provenance_must_be_verified(cfg, state, evidence, metadata, reason):
    evidence.update(modified_at="2021-01-11T20:25:25Z", raw_metadata_json=http_metadata(**metadata))
    with pytest.raises(ValueError, match=reason):
        d.prepare_state(state, [evidence], cfg)


def test_unknown_latency_is_quarantined_not_retimed(cfg, state, evidence):
    state["available_at"] = "2021-01-01T00:00:00Z"
    with pytest.raises(ValueError, match="state_availability_after_snapshot"):
        d.prepare_state(state, [evidence], cfg)


def test_future_foreign_filing_does_not_enter_old_state(cfg, state, evidence):
    later = dict(evidence, membership_id="future", evidence_available_at="2020-01-03T14:00:00Z",
                 accession_number="0000000002-20-000001", version_status="revision_observed",
                 raw_metadata_json="not-even-valid-json")
    p = d.prepare_state(state, [evidence, later], cfg)
    assert p["future_members_not_used"] == 1
    assert len(p["evidence"]) == 1
    assert p["boundary"] == state["state_time"]


def test_different_http_metadata_does_not_hide_ambiguous_raw_lineage(cfg, state, evidence):
    other = dict(evidence, modified_at="2021-01-01T00:00:00Z")
    with pytest.raises(ValueError, match="ambiguous_membership"):
        d.prepare_state(state, [evidence, other], cfg)


@pytest.mark.parametrize("field,value", [
    ("strict_pit", True), ("training_authorized", True),
    ("research_end_day", "2026-08-28"), ("clock_policy", "historical_public_proxy"),
    ("label_version", "event_distributional_close_aligned_v001"),
    ("projection_version", "event_arrival_set_v001"), ("maximum_unexplained_boundary_shift_seconds", 86400),
])
def test_contract_cannot_silently_change_clocks_versions_or_authority(cfg, field, value):
    with pytest.raises(ValueError):
        d.validate_contract(dict(cfg, **{field: value}))


@pytest.fixture
def built(tmp_path, monkeypatch, cfg, state, evidence, grid):
    source_path, market_path = tmp_path / "source.sqlite", tmp_path / "market.sqlite"
    s2 = dict(state, event_state_id="s2", event_id="event2", event_observation_id="obs2",
              observation_event_id="event2", version_event_id="event2",
              identity_key="sec:0000000001-20-000001:item:7.01")
    maintenance = "2021-01-11T20:25:25Z"
    evidence.update(modified_at=maintenance, raw_metadata_json=http_metadata(maintenance))
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
    monkeypatch.setattr(engine, "STATE_SQL", "SELECT * FROM normalized_event_state_snapshots WHERE feature_version=? ORDER BY state_time,event_state_id")
    monkeypatch.setattr(d, "EVIDENCE_SQL", "SELECT * FROM evidence WHERE normalization_run_id=? AND event_observation_id=?")
    monkeypatch.setattr(engine, "exchange_grid", lambda *args: grid)
    monkeypatch.setattr(engine, "load_prices", lambda *args: prices_and_label(grid, cfg)[0])
    monkeypatch.setattr(engine, "action_days", lambda *args: set())
    cfg = dict(cfg, delay_sensitivity_seconds=[0])
    before = (engine.file_digest(source_path), engine.file_digest(market_path))
    output = tmp_path / "result"
    report = d.build(cfg, output, source_path, market_path, ["asset_vol_63d_pct"])
    return dict(cfg=cfg, output=output, report=report, source=source_path, market=market_path,
                before=before, db=output / "dataset.sqlite")


def test_corrected_builder_reproduces_labels_without_modifying_sources(built):
    report = built["report"]
    assert report["integrity_status"] == "PASS"
    assert report["samples"] == 1
    assert report["temporal_audit"]["unexplained_shift_states"] == 0
    assert report["temporal_audit"]["rejected_v001_shifts_over_one_day"] == 2
    assert report["scenario_coverage"][0]["first_origin"] == "2020-01-02"
    assert built["before"] == (engine.file_digest(built["source"]), engine.file_digest(built["market"]))
    with sqlite3.connect(built["db"]) as conn:
        assert conn.execute("SELECT COUNT(*) FROM sample_events").fetchone()[0] == 2
        assert conn.execute("SELECT COUNT(*) FROM outcomes WHERE status='usable'").fetchone()[0] == 4
        assert conn.execute("SELECT DISTINCT label_version FROM outcomes").fetchone()[0] == built["cfg"]["label_version"]
    assert d.audit_artifact(built["output"], built["cfg"]) == report


def test_policy_isolation_preserves_legacy_algebra(cfg, state, evidence):
    evidence.update(modified_at="2021-01-11T20:25:25Z",
                    raw_metadata_json=http_metadata("2021-01-11T20:25:25Z"))
    before = engine.prepare_state
    assert d.prepare_state(state, [evidence], cfg)["boundary"] == state["state_time"]
    assert engine.prepare_state is before
    legacy = engine.prepare_state(state, [evidence], dict(cfg, clock_policy="historical_public_proxy"))
    assert legacy["boundary"].startswith("2021-01-11")


def test_repeat_build_and_audit_never_overwrite_outputs(built):
    before = {p.name: engine.file_digest(p) for p in built["output"].iterdir()}
    assert d.build(built["cfg"], built["output"], built["source"], built["market"],
                   ["asset_vol_63d_pct"]) == built["report"]
    assert before == {p.name: engine.file_digest(p) for p in built["output"].iterdir()}
    with pytest.raises(ValueError, match="audit_config"):
        d.audit_artifact(built["output"], dict(built["cfg"], minimum_assets=999))


def test_changed_input_requires_new_run_id(built):
    with sqlite3.connect(built["source"]) as conn:
        conn.execute("UPDATE normalized_event_state_snapshots SET ticker='CHANGED'")
    with pytest.raises(ValueError, match="inputs_changed"):
        d.build(built["cfg"], built["output"], built["source"], built["market"], ["asset_vol_63d_pct"])


def test_http_date_cannot_change_features_sample_identity_or_outcomes(built, tmp_path):
    with sqlite3.connect(built["source"]) as conn:
        conn.execute("UPDATE evidence SET modified_at=?,raw_metadata_json=?",
                     ("2022-05-11T20:25:25Z", http_metadata("2022-05-11T20:25:25Z")))
    output = tmp_path / "different_http_date"
    d.build(built["cfg"], output, built["source"], built["market"], ["asset_vol_63d_pct"])
    with sqlite3.connect(built["db"]) as a, sqlite3.connect(output / "dataset.sqlite") as b:
        for table in ("samples", "outcomes", "sample_events", "sample_groups"):
            assert a.execute(f"SELECT * FROM {table}").fetchall() == b.execute(f"SELECT * FROM {table}").fetchall()


@pytest.mark.parametrize("statement,reason", [
    ("UPDATE state_audit SET payload_json=json_set(payload_json,'$.boundary','2021-01-11T20:25:25+00:00')", "UNEXPLAINED_INFORMATION_BOUNDARY_SHIFT"),
    ("UPDATE state_audit SET payload_json=json_set(payload_json,'$.boundary_shift_seconds',999)", "UNEXPLAINED_INFORMATION_BOUNDARY_SHIFT"),
    ("UPDATE state_audit SET payload_json=json_set(payload_json,'$.clock_policy','old')", "CLOCK_PROVENANCE_POLICY_MISMATCH"),
    ("UPDATE samples SET information_cutoff='2020-01-02T15:00:00Z'", "INFORMATION_CUTOFF_PROVENANCE_MISMATCH"),
    ("DELETE FROM sample_events WHERE event_state_id='s2'", "SELECTED_ALIGNMENT_SAMPLE_LINK_COUNT_MISMATCH"),
    ("DELETE FROM alignment_audit WHERE event_state_id='s2'", "INCOMPLETE_OR_FOREIGN_STATE_ALIGNMENT"),
    ("UPDATE sample_events SET accession='other'", "SAMPLE_EVENT_IDENTITY_OR_BOUNDARY_MISMATCH"),
    ("UPDATE outcomes SET return_pct=999 WHERE horizon_sessions=1", "OUTCOME_REPLAY_MISMATCH"),
])
def test_independent_persisted_audit_rejects_corruption(built, statement, reason):
    with sqlite3.connect(built["db"]) as conn:
        conn.execute(statement)
    assert reason in d.audit(built["db"])["failures"]


def test_sidecar_and_database_manifest_must_agree(built):
    path = built["output"] / "manifest.json"
    value = json.loads(path.read_text())
    value["source_state_count"] = 99999
    path.write_text(json.dumps(value))
    with pytest.raises(ValueError, match="sidecar_database"):
        d.audit_artifact(built["output"], built["cfg"])


def test_quarantine_reports_only_asof_foreign_memberships(built, tmp_path):
    with sqlite3.connect(built["source"]) as conn:
        conn.row_factory = sqlite3.Row
        row = dict(conn.execute("SELECT * FROM evidence WHERE event_observation_id='obs1'").fetchone())
        row.update(membership_id="foreign", accession_number="0000000002-19-000001",
                   raw_document_id="foreign_raw", raw_sha256="b" * 64, match_method="exact_text")
        engine.insert_dict(conn, "evidence", row)
        engine.insert_dict(conn, "evidence", dict(row, membership_id="future_foreign",
            accession_number="0000000003-20-000001", evidence_available_at="2020-01-03T14:00:00Z"))
    report = d.build(built["cfg"], tmp_path / "quarantine", built["source"], built["market"], ["asset_vol_63d_pct"])
    assert report["state_exclusions"] == {"cross_accession_evidence_requires_review": 1}
    assert "AMBIGUOUS_FILING_LINKS_QUARANTINED" in report["review"]
    detail = report["lineage_review"]["details"][0]
    assert detail["foreign_accessions"] == ["0000000002-19-000001"]
    assert detail["own_memberships"] == 1 and detail["foreign_memberships"] == 1
    assert detail["future_members_not_used"] == 1
    assert report["temporal_audit"]["eligible_states"] == 1


def test_corporate_selection_gets_an_explicit_review_flag(built):
    with sqlite3.connect(built["db"]) as conn:
        conn.execute("UPDATE outcomes SET status='excluded',reason='corporate_action_overlap',"
                     "return_pct=NULL,mfe_pct=NULL,mae_pct=NULL,realized_path_vol_pct=NULL WHERE horizon_sessions=10")
    assert "CORPORATE_ACTION_SELECTION_SCENARIO_0_H10" in d.audit(built["db"])["review"]


def test_missing_exact_market_origin_is_not_replaced_by_a_later_day(built, tmp_path):
    with sqlite3.connect(built["market"]) as conn:
        conn.execute("DELETE FROM market_daily_v003_states WHERE trading_day='2020-01-02'")
    output = tmp_path / "missing_origin"
    report = d.build(built["cfg"], output, built["source"], built["market"], ["asset_vol_63d_pct"])
    assert report["samples"] == 0
    assert report["integrity_status"] == "FAIL"
    assert "SOURCE_YEAR_WITHOUT_SELECTED_BASE_STATES_2020" in report["review"]
    with sqlite3.connect(output / "dataset.sqlite") as conn:
        assert conn.execute("SELECT DISTINCT origin_day,status,reason FROM alignment_audit").fetchall() == [
            ("2020-01-02", "excluded", "missing_or_ambiguous_exact_market_state")]


def test_legacy_cli_cannot_build_new_invalid_datasets(monkeypatch, capsys):
    monkeypatch.setattr("sys.argv", ["legacy", "--stage", "build", "--run-id", "must_not_exist"])
    with pytest.raises(SystemExit) as exc:
        engine.main()
    assert exc.value.code == 2
    assert "V001 superseded" in capsys.readouterr().err


def test_http_clock_diagnostics_are_not_features(built):
    with sqlite3.connect(built["db"]) as conn:
        market, event = conn.execute("SELECT market_features_json,event_features_json FROM samples").fetchone()
    assert set(json.loads(market)).issubset(engine.MARKET_ALLOWLIST)
    assert set(json.loads(event)) == {*engine.EVENT_NUMERIC, "event_types"}
    assert set(json.loads(event)).isdisjoint({*engine.TARGETS, "modified_at", "retrieved_at", "clock_diagnostic"})


def test_nontext_http_header_is_quarantined_as_unknown_provenance(cfg, state, evidence):
    evidence.update(modified_at="2021-01-11T20:25:25Z",
                    raw_metadata_json=http_metadata(response_headers={"last-modified": 123}))
    with pytest.raises(ValueError, match="unverified_http_modified_metadata"):
        d.prepare_state(state, [evidence], cfg)


def test_incomplete_output_is_never_overwritten(built, tmp_path):
    output = tmp_path / "incomplete"
    output.mkdir()
    sentinel = output / "dataset.sqlite"
    sentinel.write_bytes(b"prior-incomplete-output")
    with pytest.raises(ValueError, match="incomplete_output"):
        d.build(built["cfg"], output, built["source"], built["market"], ["asset_vol_63d_pct"])
    assert sentinel.read_bytes() == b"prior-incomplete-output"

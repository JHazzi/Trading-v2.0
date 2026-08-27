from __future__ import annotations

import json
import sqlite3
from pathlib import Path

import pytest

from ingestion.expectations.foundation_v001 import (
    CaptureContractError,
    audit_db,
    init_db,
    ingest_records,
    manifest,
    validate_record,
)


def schema_path() -> Path:
    return Path(__file__).resolve().parents[1] / "database" / "information_capture_v001_schema.sql"


def source(strict: bool = True) -> dict:
    return {
        "kind": "source_observation",
        "payload": {
            "observation_id": "src-1",
            "source_type": "provider_api",
            "source_name": "example",
            "source_ref": "snapshot/1",
            "published_at": "2026-08-27T17:00:00-03:00",
            "first_seen_at": "2026-08-27T17:05:00-03:00" if strict else None,
            "retrieved_at": "2026-08-27T17:05:02-03:00",
            "available_at": "2026-08-27T17:05:02-03:00" if strict else "2026-08-27T17:00:00-03:00",
            "strict_pit": strict,
            "content_sha256": "abc",
            "raw_payload_json": {"x": 1},
        },
    }


def expectation(strict: bool = True) -> dict:
    return {
        "kind": "expectation_observation",
        "payload": {
            "observation_id": "exp-1",
            "entity_key": "AAPL",
            "asset_ticker": "AAPL",
            "expectation_type": "analyst_consensus",
            "metric_key": "eps_diluted",
            "fiscal_period": "2026Q4",
            "statistic_key": "mean",
            "value_real": 2.05,
            "unit": "USD/share",
            "provider_as_of": "2026-08-27T16:59:00-03:00",
            "available_at": "2026-08-27T17:05:02-03:00",
            "strict_pit": strict,
            "source_observation_id": "src-1",
        },
    }


def test_init_and_audit_empty(tmp_path: Path) -> None:
    db = tmp_path / "capture.db"
    init_db(db, schema_path())
    a = audit_db(db)
    assert a["status"] == "PASS"
    assert a["feature_visibility"] == "BLOCKED"


def test_strict_source_cannot_claim_availability_before_retrieval() -> None:
    r = source(True)
    r["payload"]["available_at"] = "2026-08-27T17:00:01-03:00"
    with pytest.raises(CaptureContractError):
        validate_record(r)


def test_historical_backfill_may_be_non_strict(tmp_path: Path) -> None:
    db = tmp_path / "capture.db"
    init_db(db, schema_path())
    stats = ingest_records(db, [source(False)])
    assert stats["inserted"] == 1
    assert audit_db(db)["non_strict_pit_rows"] == 1


def test_child_lineage_and_strictness(tmp_path: Path) -> None:
    db = tmp_path / "capture.db"
    init_db(db, schema_path())
    ingest_records(db, [source(True), expectation(True)])
    a = audit_db(db)
    assert a["counts"]["expectation_observations"] == 1
    assert a["orphan_child_rows"] == 0


def test_strict_child_cannot_depend_on_non_strict_source(tmp_path: Path) -> None:
    db = tmp_path / "capture.db"
    init_db(db, schema_path())
    ingest_records(db, [source(False)])
    with pytest.raises(CaptureContractError):
        ingest_records(db, [expectation(True)])


def test_append_only_idempotent_same_observation(tmp_path: Path) -> None:
    db = tmp_path / "capture.db"
    init_db(db, schema_path())
    stats1 = ingest_records(db, [source(True), expectation(True)])
    stats2 = ingest_records(db, [source(True), expectation(True)])
    assert stats1["inserted"] == 2
    assert stats2["inserted"] == 0
    assert stats2["skipped"] == 2


def test_revision_is_new_observation(tmp_path: Path) -> None:
    db = tmp_path / "capture.db"
    init_db(db, schema_path())
    src2 = source(True)
    src2["payload"]["observation_id"] = "src-2"
    src2["payload"]["retrieved_at"] = "2026-08-27T18:05:02-03:00"
    src2["payload"]["available_at"] = "2026-08-27T18:05:02-03:00"
    exp2 = expectation(True)
    exp2["payload"]["observation_id"] = "exp-2"
    exp2["payload"]["value_real"] = 2.08
    exp2["payload"]["available_at"] = "2026-08-27T18:05:02-03:00"
    exp2["payload"]["source_observation_id"] = "src-2"
    ingest_records(db, [source(True), expectation(True), src2, exp2])
    assert audit_db(db)["expectation_revision_series"] == 1


def test_manifest_hash_is_deterministic(tmp_path: Path) -> None:
    db = tmp_path / "capture.db"
    init_db(db, schema_path())
    ingest_records(db, [source(True), expectation(True)])
    assert manifest(db)["canonical_sha256"] == manifest(db)["canonical_sha256"]

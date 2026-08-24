import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import ingestion.events.sec_edgar_v2 as sec_edgar
from database.apply_migration_011 import apply
from ingestion.events.sec_edgar_v2 import (
    RawStore,
    archive_document_url,
    collect_submission_payloads,
    ingest_target,
    iter_columnar_filings,
    persist_filing,
    persist_submission_response,
)


def _columnar_payload(
    accession: str,
    *,
    acceptance: str,
    document: str,
) -> dict:
    return {
        "accessionNumber": [accession],
        "filingDate": [acceptance[:10]],
        "acceptanceDateTime": [acceptance],
        "reportDate": [acceptance[:10]],
        "form": ["8-K"],
        "primaryDocument": [document],
        "primaryDocDescription": ["CURRENT REPORT"],
        "items": ["2.02,9.01"],
    }


class FakeSecClient:
    def __init__(self, responses: dict[str, dict]) -> None:
        self.responses = responses
        self.calls: list[str] = []

    def get_json(self, url: str) -> tuple[dict, bytes]:
        self.calls.append(url)
        parsed = self.responses[url]
        payload = json.dumps(
            parsed,
            sort_keys=True,
            separators=(",", ":"),
        ).encode("utf-8")
        return parsed, payload


def _create_contract_db(db: Path) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE assets (
                asset_id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            "INSERT INTO assets(asset_id, ticker) VALUES (1, 'AAPL')"
        )
    apply(db)


def test_columnar_sec_payload_is_normalized():
    payload = {
        "filings": {
            "recent": {
                "accessionNumber": ["0000320193-26-000001"],
                "filingDate": ["2026-01-02"],
                "acceptanceDateTime": ["2026-01-02T21:01:02Z"],
                "reportDate": ["2025-12-31"],
                "form": ["8-K"],
                "primaryDocument": ["aapl-20260102.htm"],
                "primaryDocDescription": ["CURRENT REPORT"],
                "items": ["2.02,9.01"],
            }
        }
    }

    rows = list(iter_columnar_filings(payload))

    assert len(rows) == 1
    assert rows[0]["form"] == "8-K"
    assert rows[0]["items"] == "2.02,9.01"


def test_archive_url_uses_cik_and_accession_without_padding():
    result = archive_document_url(
        "0000320193",
        "0000320193-26-000001",
        "aapl-20260102.htm",
    )

    assert result == (
        "https://www.sec.gov/Archives/edgar/data/320193/"
        "000032019326000001/aapl-20260102.htm"
    )


def test_migration_and_filing_persistence_are_reproducible(tmp_path):
    db = tmp_path / "market.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE assets (
                asset_id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            "INSERT INTO assets(asset_id, ticker) VALUES (1, 'AAPL')"
        )

    result = apply(db)
    assert result["status"] == "applied"

    row = {
        "accessionNumber": "0000320193-26-000001",
        "filingDate": "2026-01-02",
        "acceptanceDateTime": "2026-01-02T21:01:02Z",
        "reportDate": "2025-12-31",
        "form": "8-K",
        "primaryDocument": "aapl-20260102.htm",
        "primaryDocDescription": "CURRENT REPORT",
        "items": "2.02,9.01",
    }
    store = RawStore(tmp_path / "raw")
    source_bytes = b'{"cik":"0000320193","filings":{"recent":{}}}\n'

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        parent_id = persist_submission_response(
            conn,
            store,
            cik="320193",
            source_url=(
                "https://data.sec.gov/submissions/CIK0000320193.json"
            ),
            external_id="submissions/CIK0000320193.json",
            storage_name="CIK0000320193.json",
            payload=source_bytes,
            retrieved_at="2026-08-24T12:00:00+00:00",
        )
        inserted = persist_filing(
            conn,
            store,
            cik="320193",
            ticker="AAPL",
            entity_name="Apple Inc.",
            row=row,
            retrieved_at="2026-08-24T12:00:00+00:00",
            parent_raw_document_id=parent_id,
        )
        duplicate = persist_filing(
            conn,
            store,
            cik="320193",
            ticker="AAPL",
            entity_name="Apple Inc.",
            row=row,
            retrieved_at="2026-08-24T12:01:00+00:00",
            parent_raw_document_id=parent_id,
        )
        conn.commit()

        filing = conn.execute(
            """
            SELECT accession_number, acceptance_datetime, items_json
            FROM sec_filings
            """
        ).fetchone()
        raw = conn.execute(
            """
            SELECT storage_path, source_url, available_at,
                   parent_raw_document_id, document_kind, parser_status
            FROM raw_source_documents
            WHERE document_kind = 'sec_filing_metadata_normalized'
            """
        ).fetchone()
        source_raw = conn.execute(
            """
            SELECT storage_path, raw_sha256, document_kind, parser_status
            FROM raw_source_documents
            WHERE raw_document_id = ?
            """,
            (parent_id,),
        ).fetchone()
        asset_links = conn.execute(
            "SELECT COUNT(*) FROM raw_document_assets"
        ).fetchone()[0]
        raw_count = conn.execute(
            "SELECT COUNT(*) FROM raw_source_documents"
        ).fetchone()[0]

    assert inserted is True
    assert duplicate is False
    assert filing[0] == "0000320193-26-000001"
    assert filing[1] == "2026-01-02T21:01:02+00:00"
    assert json.loads(filing[2]) == ["2.02", "9.01"]
    assert raw[1].endswith("/aapl-20260102.htm")
    assert raw[2] == filing[1]
    assert asset_links == 1
    assert raw[3] == parent_id
    assert raw[4] == "sec_filing_metadata_normalized"
    assert raw[5] == "parsed"
    assert raw_count == 2
    assert source_raw[2:] == ("sec_submissions_json", "raw")

    stored_path = Path(raw[0])
    assert stored_path.exists()
    with gzip.open(stored_path, "rb") as stream:
        stored = json.loads(stream.read().decode("utf-8"))
    assert stored["accessionNumber"] == filing[0]

    source_path = Path(source_raw[0])
    with gzip.open(source_path, "rb") as stream:
        stored_source = stream.read()
    assert stored_source == source_bytes
    assert source_raw[1] == hashlib.sha256(source_bytes).hexdigest()



def test_migration_preserves_legacy_ingestion_runs(tmp_path):
    db = tmp_path / "market.db"
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            CREATE TABLE assets (
                asset_id INTEGER PRIMARY KEY,
                ticker TEXT NOT NULL,
                active INTEGER NOT NULL DEFAULT 1
            )
            """
        )
        conn.execute(
            """
            CREATE TABLE ingestion_runs (
                run_id INTEGER PRIMARY KEY AUTOINCREMENT,
                worker_name TEXT NOT NULL,
                started_at TEXT NOT NULL,
                finished_at TEXT,
                status TEXT NOT NULL,
                rows_inserted INTEGER DEFAULT 0,
                rows_skipped INTEGER DEFAULT 0,
                error_count INTEGER DEFAULT 0,
                metadata_json TEXT
            )
            """
        )
        conn.execute(
            """
            INSERT INTO ingestion_runs(
                worker_name, started_at, status, rows_inserted
            )
            VALUES (?, ?, ?, ?)
            """,
            ("legacy_worker", "2026-08-01T00:00:00+00:00", "completed", 7),
        )

    apply(db)
    apply(db)

    with sqlite3.connect(db) as conn:
        legacy_columns = [
            row[1]
            for row in conn.execute("PRAGMA table_info(ingestion_runs)")
        ]
        legacy_row = conn.execute(
            "SELECT worker_name, rows_inserted FROM ingestion_runs"
        ).fetchone()
        source_run_count = conn.execute(
            "SELECT COUNT(*) FROM source_ingestion_runs"
        ).fetchone()[0]
        source_count = conn.execute(
            "SELECT COUNT(*) FROM ingestion_sources"
        ).fetchone()[0]

    assert "worker_name" in legacy_columns
    assert "source_id" not in legacy_columns
    assert legacy_row == ("legacy_worker", 7)
    assert source_run_count == 0
    assert source_count == 1


def test_raw_store_is_immutable_and_rejects_path_collisions(tmp_path):
    store = RawStore(tmp_path / "raw")
    relative_path = Path("sec/submissions/fixed.json.gz")
    original = b'{"value":1}\n'

    first = store.write_json(relative_path, original)
    repeated = store.write_json(relative_path, original)

    assert repeated == first
    assert first.sha256 == hashlib.sha256(original).hexdigest()
    assert first.byte_length == len(original)

    for different_payload in (b'{"value":2}\n', b'{"value":200}\n'):
        with pytest.raises(FileExistsError, match="Colisión de ruta raw"):
            store.write_json(relative_path, different_payload)

    with gzip.open(first.path, "rb") as stream:
        assert stream.read() == original


def test_submission_collection_downloads_older_shards_lazily(monkeypatch):
    cik = "0000320193"
    current_url = sec_edgar.SUBMISSIONS_URL.format(cik=cik)
    first_name = "CIK0000320193-submissions-001.json"
    second_name = "CIK0000320193-submissions-002.json"
    first_url = sec_edgar.SUBMISSION_FILE_URL.format(name=first_name)
    second_url = sec_edgar.SUBMISSION_FILE_URL.format(name=second_name)
    current = {
        "filings": {
            "recent": {"accessionNumber": []},
            "files": [{"name": first_name}, {"name": second_name}],
        }
    }
    client = FakeSecClient(
        {
            current_url: current,
            first_url: {"accessionNumber": []},
            second_url: {"accessionNumber": []},
        }
    )
    timestamps = iter(
        [
            "2026-08-24T12:00:01+00:00",
            "2026-08-24T12:00:02+00:00",
            "2026-08-24T12:00:03+00:00",
        ]
    )
    monkeypatch.setattr(sec_edgar, "utc_now", lambda: next(timestamps))

    payloads = collect_submission_payloads(
        client,
        cik=cik,
        include_older=True,
    )

    assert client.calls == []

    current_payload = next(payloads)
    assert client.calls == [current_url]
    assert current_payload.retrieved_at == "2026-08-24T12:00:01+00:00"

    first_payload = next(payloads)
    assert client.calls == [current_url, first_url]
    assert first_payload.retrieved_at == "2026-08-24T12:00:02+00:00"
    assert second_url not in client.calls


def test_max_filings_stops_before_unused_older_shards(
    tmp_path,
    monkeypatch,
):
    cik = "0000320193"
    current_url = sec_edgar.SUBMISSIONS_URL.format(cik=cik)
    older_name = "CIK0000320193-submissions-001.json"
    older_url = sec_edgar.SUBMISSION_FILE_URL.format(name=older_name)
    current = {
        "name": "Apple Inc.",
        "filings": {
            "recent": _columnar_payload(
                "0000320193-26-000001",
                acceptance="2026-01-02T21:01:02Z",
                document="aapl-20260102.htm",
            ),
            "files": [{"name": older_name}],
        },
    }
    client = FakeSecClient({current_url: current})
    monkeypatch.setattr(
        sec_edgar,
        "utc_now",
        lambda: "2026-08-24T12:00:01+00:00",
    )
    db = tmp_path / "market.db"
    _create_contract_db(db)

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        discovered, inserted = ingest_target(
            conn,
            RawStore(tmp_path / "raw"),
            client,
            cik=cik,
            ticker="AAPL",
            entity_name="Apple Inc.",
            forms={"8-K"},
            max_filings=1,
            include_older=True,
        )
        conn.commit()

    assert (discovered, inserted) == (1, 1)
    assert client.calls == [current_url]
    assert older_url not in client.calls


def test_each_submission_timestamp_is_captured_after_its_request_and_persisted(
    tmp_path,
    monkeypatch,
):
    cik = "0000320193"
    current_url = sec_edgar.SUBMISSIONS_URL.format(cik=cik)
    older_name = "CIK0000320193-submissions-001.json"
    older_url = sec_edgar.SUBMISSION_FILE_URL.format(name=older_name)
    current_accession = "0000320193-26-000001"
    older_accession = "0000320193-25-000002"
    current = {
        "name": "Apple Inc.",
        "filings": {
            "recent": _columnar_payload(
                current_accession,
                acceptance="2026-01-02T21:01:02Z",
                document="aapl-20260102.htm",
            ),
            "files": [{"name": older_name}],
        },
    }
    older = _columnar_payload(
        older_accession,
        acceptance="2025-12-01T15:30:00Z",
        document="aapl-20251201.htm",
    )
    client = FakeSecClient(
        {
            current_url: current,
            older_url: older,
        }
    )
    expected_timestamps = [
        "2026-08-24T12:00:01+00:00",
        "2026-08-24T12:00:02+00:00",
    ]
    timestamp_calls: list[str] = []

    def now_after_request() -> str:
        assert len(client.calls) == len(timestamp_calls) + 1
        value = expected_timestamps[len(timestamp_calls)]
        timestamp_calls.append(value)
        return value

    monkeypatch.setattr(sec_edgar, "utc_now", now_after_request)
    db = tmp_path / "market.db"
    _create_contract_db(db)

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        discovered, inserted = ingest_target(
            conn,
            RawStore(tmp_path / "raw"),
            client,
            cik=cik,
            ticker="AAPL",
            entity_name="Apple Inc.",
            forms={"8-K"},
            max_filings=None,
            include_older=True,
        )
        conn.commit()

        source_timestamps = dict(
            conn.execute(
                """
                SELECT external_id, retrieved_at
                FROM raw_source_documents
                WHERE document_kind = 'sec_submissions_json'
                """
            )
        )
        filing_timestamps = dict(
            conn.execute(
                """
                SELECT filing.accession_number, raw.retrieved_at
                FROM sec_filings AS filing
                JOIN raw_source_documents AS raw
                  ON raw.raw_document_id = filing.raw_document_id
                """
            )
        )

    assert (discovered, inserted) == (2, 2)
    assert client.calls == [current_url, older_url]
    assert timestamp_calls == expected_timestamps
    assert source_timestamps == {
        "submissions/CIK0000320193.json": expected_timestamps[0],
        f"submissions/{older_name}": expected_timestamps[1],
    }
    assert filing_timestamps == {
        current_accession: expected_timestamps[0],
        older_accession: expected_timestamps[1],
    }

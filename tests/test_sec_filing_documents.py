import gzip
import hashlib
import http.client
import json
import sys
import urllib.error
import urllib.request
import sqlite3
from pathlib import Path

import pytest
import database.apply_migration_012 as migration_012

from database.apply_migration_011 import apply as apply_011
from database.apply_migration_012 import apply as apply_012
from database.apply_migration_014 import apply as apply_014
import ingestion.events.sec_filing_documents as sec_documents
from ingestion.events.sec_edgar_v2 import (
    RawStore,
    persist_filing,
    persist_submission_response,
)
from ingestion.events.sec_filing_documents import (
    ByteBudget,
    ContentAddressedRawStore,
    FilingRecord,
    InventoryFile,
    RawIntegrityError,
    ResponseTooLarge,
    SecArchiveError,
    SecArchiveClient,
    SecResponse,
    _SecRedirectHandler,
    _decode_gzip_limited,
    _supersede_absent_inventory_rows,
    _upsert_inventory_file,
    archive_file_url,
    archive_index_url,
    parse_filing_index,
    execute_ingestion_run,
    process_filing,
    safe_document_name,
    select_filings,
    selected_documents,
    with_primary_flag,
)


ACCESSION = "0000320193-26-000018"
ACCEPTANCE = "2026-07-30T20:30:28+00:00"
PRIMARY_NAME = "aapl-20260730.htm"
EXHIBIT_NAME = "aapl-20260730xex99d1.htm"
GRAPHIC_NAME = "logo.jpg"

INDEX_HTML = f"""<!doctype html>
<html><body>
<table class="tableFile" summary="Document Format Files">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>1</td><td>CURRENT REPORT</td><td><a href="{PRIMARY_NAME}">{PRIMARY_NAME}</a></td><td>8-K</td><td>120</td></tr>
<tr><td>2</td><td>PRESS RELEASE</td><td><a href="{EXHIBIT_NAME}">{EXHIBIT_NAME}</a></td><td>EX-99.1</td><td>80</td></tr>
</table>
<table class="tableFile" summary="Data Files">
<tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th><th>Size</th></tr>
<tr><td>3</td><td>LOGO</td><td><a href="{GRAPHIC_NAME}">{GRAPHIC_NAME}</a></td><td>GRAPHIC</td><td>40</td></tr>
</table>
</body></html>""".encode()


class FakeClient:
    def __init__(self, payloads: dict[str, bytes], timestamp_prefix: str) -> None:
        self.payloads = payloads
        self.timestamp_prefix = timestamp_prefix
        self.calls: list[tuple[str, int]] = []

    def get_bytes(self, url: str, *, max_bytes: int) -> SecResponse:
        self.calls.append((url, max_bytes))
        payload = self.payloads[url]
        if len(payload) > max_bytes:
            raise ResponseTooLarge("fake response too large")
        sequence = len(self.calls)
        return SecResponse(
            requested_url=url,
            final_url=url,
            payload=payload,
            retrieved_at=f"{self.timestamp_prefix}:{sequence:02d}+00:00",
            content_type=(
                "text/html" if url.endswith(".html") else "application/octet-stream"
            ),
            headers={"content-length": str(len(payload))},
        )


def _create_database(tmp_path: Path) -> tuple[Path, FilingRecord]:
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

    apply_011(db)
    apply_012(db)
    apply_012(db)

    apply_014(db)
    metadata_store = RawStore(tmp_path / "metadata_raw")
    row = {
        "accessionNumber": ACCESSION,
        "filingDate": "2026-07-30",
        "acceptanceDateTime": "2026-07-30T20:30:28Z",
        "reportDate": "2026-07-30",
        "form": "8-K",
        "primaryDocument": PRIMARY_NAME,
        "primaryDocDescription": "CURRENT REPORT",
        "items": "2.02,9.01",
    }
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        parent = persist_submission_response(
            conn,
            metadata_store,
            cik="320193",
            source_url="https://data.sec.gov/submissions/CIK0000320193.json",
            external_id="submissions/CIK0000320193.json",
            storage_name="CIK0000320193.json",
            payload=b'{"filings":{"recent":{}}}',
            retrieved_at="2026-08-24T10:00:00+00:00",
        )
        assert persist_filing(
            conn,
            metadata_store,
            cik="320193",
            ticker="AAPL",
            entity_name="Apple Inc.",
            row=row,
            retrieved_at="2026-08-24T10:00:00+00:00",
            parent_raw_document_id=parent,
        )
        conn.commit()
        filing = select_filings(conn, accessions=[ACCESSION], max_filings=1)[0]

    return db, filing


def _insert_run(conn: sqlite3.Connection, run_id: str) -> None:
    conn.execute(
        """
        INSERT INTO source_ingestion_runs(
            run_id, source_id, mode, started_at, status
        )
        VALUES (?, 'sec_edgar', 'test_filing_documents',
                '2026-08-24T11:00:00+00:00', 'running')
        """,
        (run_id,),
    )
    conn.commit()


def _payloads(primary: bytes = b"<html>primary v1</html>") -> dict[str, bytes]:
    return {
        archive_index_url("320193", ACCESSION): INDEX_HTML,
        archive_file_url("320193", ACCESSION, PRIMARY_NAME): primary,
        archive_file_url("320193", ACCESSION, EXHIBIT_NAME): b"press release",
    }


def test_index_parser_and_default_selection_are_deterministic():
    inventory = with_primary_flag(
        parse_filing_index(INDEX_HTML),
        PRIMARY_NAME,
        primary_description="CURRENT REPORT",
        form="8-K",
    )
    selected, skipped = selected_documents(inventory, max_files=10)

    assert len(inventory) == 3
    assert [(item.document_name, reason) for item, reason in selected] == [
        (PRIMARY_NAME, "primary"),
        (EXHIBIT_NAME, "exhibit"),
    ]
    assert skipped == []
    assert inventory[2].table_section == "Data Files"


def test_inline_xbrl_label_does_not_pollute_document_name():
    inline_href = (
        "/ix?doc=/Archives/edgar/data/320193/"
        "000032019326000018/aapl-20260730.htm"
    )
    inline_html = INDEX_HTML.replace(
        f'href="{PRIMARY_NAME}">{PRIMARY_NAME}</a>'.encode(),
        (
            f'href="{inline_href}">{PRIMARY_NAME}</a> iXBRL'
        ).encode(),
    )

    inventory = with_primary_flag(
        parse_filing_index(inline_html),
        PRIMARY_NAME,
        primary_description="CURRENT REPORT",
        form="8-K",
    )

    assert len(inventory) == 3
    assert inventory[0].document_name == PRIMARY_NAME
    assert inventory[0].is_primary is True


def test_archive_paths_preserve_subject_cik_and_reject_traversal():
    form_4_url = archive_file_url(
        "0000320193",
        "0001140361-26-033928",
        "xslF345X06/form4.xml",
    )
    assert "/edgar/data/320193/000114036126033928/" in form_4_url
    assert form_4_url.endswith("/xslF345X06/form4.xml")

    with pytest.raises(ValueError):
        safe_document_name("../secret.txt")
    with pytest.raises(ValueError):
        safe_document_name("folder\\secret.txt")


def test_migration_012_is_idempotent_and_additive(tmp_path):
    db, _ = _create_database(tmp_path)

    with sqlite3.connect(db) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(sec_filing_files)")
        }
        version = conn.execute(
            "SELECT name FROM schema_migrations WHERE version = '012'"
        ).fetchone()
        legacy_run_table = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'ingestion_runs'
            """
        ).fetchone()

    assert {
        "inventory_raw_document_id",
        "declared_size_bytes",
        "download_status",
        "selection_reason",
        "error_json",
    } <= columns
    assert version == ("sec_filing_documents",)
    assert legacy_run_table is None


def test_content_addressed_store_reuses_and_verifies_bytes(tmp_path):
    store = ContentAddressedRawStore(tmp_path / "raw")
    kwargs = {
        "category": "documents",
        "year": "2026",
        "cik": "320193",
        "accession_number": ACCESSION,
        "payload": b"exact official bytes",
    }

    first = store.write(**kwargs)
    second = store.write(**kwargs)

    assert first == second
    assert first.sha256 == hashlib.sha256(kwargs["payload"]).hexdigest()
    assert first.sha256 in first.path.name
    with gzip.open(first.path, "rb") as stream:
        assert stream.read() == kwargs["payload"]

    first.path.write_bytes(gzip.compress(b"corrupted", mtime=0))
    with pytest.raises(RawIntegrityError):
        store.write(**kwargs)


def test_document_ingestion_is_causal_idempotent_and_retains_revision(tmp_path):
    db, filing = _create_database(tmp_path)
    document_store = ContentAddressedRawStore(tmp_path / "document_raw")

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _insert_run(conn, "run-1")
        first_client = FakeClient(_payloads(), "2026-08-24T12:00")
        first = process_filing(
            conn,
            document_store,
            first_client,
            filing,
            run_id="run-1",
            budget=ByteBudget(1_000_000),
            max_files=10,
            max_file_bytes=10_000,
            max_index_bytes=100_000,
        )
        conn.commit()

        inventory = conn.execute(
            """
            SELECT document_name, is_primary, document_type, download_status,
                   raw_document_id, inventory_raw_document_id
            FROM sec_filing_files
            ORDER BY sequence_number
            """
        ).fetchall()
        primary_raw_id = next(row[4] for row in inventory if row[0] == PRIMARY_NAME)
        primary_raw = conn.execute(
            """
            SELECT source_url, available_at, retrieved_at, raw_sha256,
                   parent_raw_document_id, storage_path
            FROM raw_source_documents
            WHERE raw_document_id = ?
            """,
            (primary_raw_id,),
        ).fetchone()
        index_raw_id = inventory[0][5]
        raw_count_first = conn.execute(
            "SELECT COUNT(*) FROM raw_source_documents"
        ).fetchone()[0]
        asset_links = conn.execute(
            """
            SELECT COUNT(*) FROM raw_document_assets
            WHERE raw_document_id IN (
                SELECT raw_document_id FROM sec_filing_files
                WHERE raw_document_id IS NOT NULL
            )
            """
        ).fetchone()[0]

        _insert_run(conn, "run-2")
        rerun_client = FakeClient(_payloads(), "2026-08-24T13:00")
        second = process_filing(
            conn,
            document_store,
            rerun_client,
            filing,
            run_id="run-2",
            budget=ByteBudget(1_000_000),
            max_files=10,
            max_file_bytes=10_000,
            max_index_bytes=100_000,
        )
        conn.commit()
        raw_count_second = conn.execute(
            "SELECT COUNT(*) FROM raw_source_documents"
        ).fetchone()[0]

        _insert_run(conn, "run-3")
        revision_client = FakeClient(
            _payloads(primary=b"<html>primary revised</html>"),
            "2026-08-24T14:00",
        )
        revision = process_filing(
            conn,
            document_store,
            revision_client,
            filing,
            run_id="run-3",
            budget=ByteBudget(1_000_000),
            max_files=10,
            max_file_bytes=10_000,
            max_index_bytes=100_000,
        )
        conn.commit()

        canonical_after = conn.execute(
            """
            SELECT raw_document_id, download_status
            FROM sec_filing_files
            WHERE document_name = ?
            """,
            (PRIMARY_NAME,),
        ).fetchone()
        versions = conn.execute(
            """
            SELECT v.version_status, r.available_at, r.retrieved_at, r.raw_sha256
            FROM sec_filing_file_versions v
            JOIN raw_source_documents r
              ON r.raw_document_id = v.raw_document_id
            WHERE v.document_name = ?
            ORDER BY v.observed_at
            """,
            (PRIMARY_NAME,),
        ).fetchall()
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()

    assert first.documents_inserted == 2
    assert first.files_downloaded == 2
    assert [row[:4] for row in inventory] == [
        (PRIMARY_NAME, 1, "8-K", "downloaded"),
        (EXHIBIT_NAME, 0, "EX-99.1", "downloaded"),
        (GRAPHIC_NAME, 0, "GRAPHIC", "skipped_policy"),
    ]
    assert primary_raw[0].endswith(f"/{PRIMARY_NAME}")
    assert primary_raw[1] == ACCEPTANCE
    assert primary_raw[2] == "2026-08-24T12:00:02+00:00"
    assert primary_raw[4] == index_raw_id
    assert Path(primary_raw[5]).exists()
    assert asset_links == 2

    assert second.documents_inserted == 0
    assert second.documents_existing == 2
    assert raw_count_second == raw_count_first

    assert revision.revisions_observed == 1
    assert canonical_after == (primary_raw_id, "content_changed")
    assert [row[0] for row in versions] == ["canonical", "revision_observed"]
    assert versions[0][1] == ACCEPTANCE
    assert versions[1][1] == versions[1][2]
    assert versions[0][3] != versions[1][3]
    assert fk_errors == []


def test_declared_size_and_total_budget_prevent_unbounded_fetch(tmp_path):
    db, filing = _create_database(tmp_path)
    oversized_index = INDEX_HTML.replace(b"<td>80</td>", b"<td>999999</td>")
    payloads = _payloads()
    payloads[archive_index_url("320193", ACCESSION)] = oversized_index
    client = FakeClient(payloads, "2026-08-24T15:00")

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _insert_run(conn, "run-size")
        stats = process_filing(
            conn,
            ContentAddressedRawStore(tmp_path / "raw"),
            client,
            filing,
            run_id="run-size",
            budget=ByteBudget(100_000),
            max_files=10,
            max_file_bytes=1_000,
            max_index_bytes=10_000,
        )
        conn.commit()
        exhibit_status = conn.execute(
            """
            SELECT download_status
            FROM sec_filing_files
            WHERE document_name = ?
            """,
            (EXHIBIT_NAME,),
        ).fetchone()[0]

    requested_urls = [url for url, _ in client.calls]
    assert archive_file_url("320193", ACCESSION, EXHIBIT_NAME) not in requested_urls
    assert exhibit_status == "skipped_size"
    assert stats.files_skipped == 2


def test_sec_client_enforces_conservative_rate_limit():
    with pytest.raises(ValueError):
        SecArchiveClient(
            "QuantMarketAI valid@example.org",
            rate_limit_per_second=5.1,
        )


def _create_legacy_012_database(tmp_path: Path, name: str) -> Path:
    db = tmp_path / name
    with sqlite3.connect(db) as conn:
        conn.executescript(
            """
            PRAGMA foreign_keys = ON;

            CREATE TABLE schema_migrations (
                version TEXT PRIMARY KEY,
                name TEXT NOT NULL
            );

            CREATE TABLE raw_source_documents (
                raw_document_id TEXT PRIMARY KEY
            );

            CREATE TABLE source_ingestion_runs (
                run_id TEXT PRIMARY KEY
            );

            CREATE TABLE sec_filings (
                raw_document_id TEXT PRIMARY KEY,
                FOREIGN KEY(raw_document_id)
                    REFERENCES raw_source_documents(raw_document_id)
            );

            CREATE TABLE sec_filing_files (
                filing_raw_document_id TEXT NOT NULL,
                sequence_number TEXT NOT NULL,
                document_name TEXT NOT NULL,
                document_type TEXT,
                description TEXT,
                source_url TEXT NOT NULL,
                is_primary INTEGER NOT NULL DEFAULT 0,
                raw_document_id TEXT,
                legacy_note TEXT,
                metadata_json TEXT,
                PRIMARY KEY(
                    filing_raw_document_id,
                    sequence_number,
                    document_name
                ),
                FOREIGN KEY(filing_raw_document_id)
                    REFERENCES sec_filings(raw_document_id),
                FOREIGN KEY(raw_document_id)
                    REFERENCES raw_source_documents(raw_document_id)
            );

            CREATE TABLE legacy_feature_cache (
                cache_key TEXT PRIMARY KEY,
                payload TEXT NOT NULL
            );

            INSERT INTO raw_source_documents(raw_document_id)
            VALUES ('filing-raw'), ('document-raw');
            INSERT INTO sec_filings(raw_document_id)
            VALUES ('filing-raw');
            INSERT INTO sec_filing_files(
                filing_raw_document_id,
                sequence_number,
                document_name,
                document_type,
                source_url,
                is_primary,
                raw_document_id,
                legacy_note
            )
            VALUES (
                'filing-raw',
                '1',
                'legacy.htm',
                '8-K',
                'https://www.sec.gov/Archives/legacy.htm',
                1,
                'document-raw',
                'must-survive'
            );
            INSERT INTO legacy_feature_cache(cache_key, payload)
            VALUES ('alpha', 'untouched');
            """
        )
    return db


def test_primary_fallback_is_deterministic_without_submission_metadata():
    inventory = parse_filing_index(INDEX_HTML)

    forward = with_primary_flag(
        inventory,
        None,
        primary_description=None,
        form="8-K",
    )
    reverse = with_primary_flag(
        reversed(inventory),
        None,
        primary_description=None,
        form="8-K",
    )
    missing_metadata = with_primary_flag(
        inventory,
        "missing-primary.htm",
        primary_description="metadata-only",
        form="8-K",
    )

    assert [item.document_name for item in forward if item.is_primary] == [
        PRIMARY_NAME
    ]
    assert [item.document_name for item in reverse if item.is_primary] == [
        PRIMARY_NAME
    ]
    assert missing_metadata[0].sequence_number == "__metadata_primary__"
    assert missing_metadata[0].document_name == "missing-primary.htm"


def test_modern_ixviewer_href_uses_doc_query_parameter():
    href = (
        "https://www.sec.gov/ixviewer/doc/action"
        "?doc=%2FArchives%2Fedgar%2Fdata%2F320193%2F"
        "000032019326000018%2Faapl-20260730.htm"
    )
    html = INDEX_HTML.replace(
        f'href="{PRIMARY_NAME}">{PRIMARY_NAME}</a>'.encode(),
        f'href="{href}">{PRIMARY_NAME}</a> iXBRL'.encode(),
    )

    inventory = parse_filing_index(html)

    assert inventory[0].document_name == PRIMARY_NAME


def test_content_and_observations_preserve_a_b_a_and_revision_reruns(tmp_path):
    db, filing = _create_database(tmp_path)
    store = ContentAddressedRawStore(tmp_path / "document_raw")
    content_a = b"<html>primary A</html>"
    content_b = b"<html>primary B</html>"
    sequence = [
        ("run-a1", content_a, "2026-08-24T16:00"),
        ("run-a2", content_a, "2026-08-24T17:00"),
        ("run-b1", content_b, "2026-08-24T18:00"),
        ("run-a3", content_a, "2026-08-24T19:00"),
        ("run-b2", content_b, "2026-08-24T20:00"),
    ]
    revision_counts = []

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        for run_id, primary, timestamp in sequence:
            _insert_run(conn, run_id)
            stats = process_filing(
                conn,
                store,
                FakeClient(_payloads(primary=primary), timestamp),
                filing,
                run_id=run_id,
                budget=ByteBudget(1_000_000),
                max_files=10,
                max_file_bytes=10_000,
                max_index_bytes=100_000,
            )
            revision_counts.append(stats.revisions_observed)
            conn.commit()

        observations = conn.execute(
            """
            SELECT o.observation_status, r.raw_sha256
            FROM sec_filing_file_observations o
            JOIN raw_source_documents r
              ON r.raw_document_id = o.raw_document_id
            WHERE o.document_name = ?
            ORDER BY o.observed_at
            """,
            (PRIMARY_NAME,),
        ).fetchall()
        version_count = conn.execute(
            """
            SELECT COUNT(*)
            FROM sec_filing_file_versions
            WHERE document_name = ?
            """,
            (PRIMARY_NAME,),
        ).fetchone()[0]
        inventory_observation_count = conn.execute(
            "SELECT COUNT(*) FROM sec_filing_inventory_observations"
        ).fetchone()[0]
        canonical_hash = conn.execute(
            """
            SELECT r.raw_sha256
            FROM sec_filing_files f
            JOIN raw_source_documents r
              ON r.raw_document_id = f.raw_document_id
            WHERE f.document_name = ?
            """,
            (PRIMARY_NAME,),
        ).fetchone()[0]

    hash_a = hashlib.sha256(content_a).hexdigest()
    hash_b = hashlib.sha256(content_b).hexdigest()
    assert revision_counts == [0, 0, 1, 0, 0]
    assert [row[0] for row in observations] == [
        "canonical_first_seen",
        "canonical_rerun",
        "revision_first_seen",
        "canonical_rerun",
        "revision_rerun",
    ]
    assert [row[1] for row in observations] == [
        hash_a,
        hash_a,
        hash_b,
        hash_a,
        hash_b,
    ]
    assert version_count == 2
    assert inventory_observation_count == len(sequence)
    assert canonical_hash == hash_a


def test_later_limits_do_not_degrade_download_success(tmp_path):
    db, filing = _create_database(tmp_path)
    store = ContentAddressedRawStore(tmp_path / "document_raw")

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _insert_run(conn, "run-full")
        process_filing(
            conn,
            store,
            FakeClient(_payloads(), "2026-08-24T21:00"),
            filing,
            run_id="run-full",
            budget=ByteBudget(1_000_000),
            max_files=10,
            max_file_bytes=10_000,
            max_index_bytes=100_000,
        )
        conn.commit()
        before = conn.execute(
            """
            SELECT raw_document_id, download_status, downloaded_at
            FROM sec_filing_files
            WHERE document_name = ?
            """,
            (EXHIBIT_NAME,),
        ).fetchone()

        _insert_run(conn, "run-limited")
        limited = process_filing(
            conn,
            store,
            FakeClient(_payloads(), "2026-08-24T22:00"),
            filing,
            run_id="run-limited",
            budget=ByteBudget(1_000_000),
            max_files=1,
            max_file_bytes=10_000,
            max_index_bytes=100_000,
        )
        conn.commit()
        after = conn.execute(
            """
            SELECT raw_document_id, download_status, downloaded_at,
                   inventory_status
            FROM sec_filing_files
            WHERE document_name = ?
            """,
            (EXHIBIT_NAME,),
        ).fetchone()

    assert before == after[:3]
    assert after[1] == "downloaded"
    assert after[3] == "current"
    assert limited.documents_discovered == 3


def test_absent_inventory_marks_presence_without_erasing_download(tmp_path):
    db, filing = _create_database(tmp_path)
    store = ContentAddressedRawStore(tmp_path / "document_raw")
    exhibit_row = (
        f'<tr><td>2</td><td>PRESS RELEASE</td><td><a href="{EXHIBIT_NAME}">'
        f"{EXHIBIT_NAME}</a></td><td>EX-99.1</td><td>80</td></tr>"
    ).encode()
    index_without_exhibit = INDEX_HTML.replace(exhibit_row, b"")

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _insert_run(conn, "run-inventory-full")
        process_filing(
            conn,
            store,
            FakeClient(_payloads(), "2026-08-24T23:00"),
            filing,
            run_id="run-inventory-full",
            budget=ByteBudget(1_000_000),
            max_files=10,
            max_file_bytes=10_000,
            max_index_bytes=100_000,
        )
        conn.commit()

        payloads = _payloads()
        payloads[archive_index_url("320193", ACCESSION)] = index_without_exhibit
        _insert_run(conn, "run-inventory-short")
        shortened = process_filing(
            conn,
            store,
            FakeClient(payloads, "2026-08-25T00:00"),
            filing,
            run_id="run-inventory-short",
            budget=ByteBudget(1_000_000),
            max_files=10,
            max_file_bytes=10_000,
            max_index_bytes=100_000,
        )
        conn.commit()
        status = conn.execute(
            """
            SELECT download_status, inventory_status, raw_document_id
            FROM sec_filing_files
            WHERE document_name = ?
            """,
            (EXHIBIT_NAME,),
        ).fetchone()

    assert shortened.documents_discovered == 2
    assert status[0] == "downloaded"
    assert status[1] == "superseded"
    assert status[2] is not None


def test_gzip_decoder_enforces_decompressed_limit():
    payload = gzip.compress(b"x" * 1_000_000, mtime=0)

    with pytest.raises(ResponseTooLarge):
        _decode_gzip_limited(payload, 32)

    assert _decode_gzip_limited(gzip.compress(b"exact", mtime=0), 5) == b"exact"


def test_redirect_handler_rejects_external_target_before_following():
    handler = _SecRedirectHandler()
    request = urllib.request.Request(
        "https://www.sec.gov/Archives/source.html"
    )

    with pytest.raises(ValueError):
        handler.redirect_request(
            request,
            None,
            302,
            "Found",
            {},
            "https://example.org/steal",
        )

    redirected = handler.redirect_request(
        request,
        None,
        302,
        "Found",
        {},
        "/Archives/allowed.html",
    )
    assert redirected.full_url == "https://www.sec.gov/Archives/allowed.html"


class _TransportResponse:
    def __init__(self, payload: bytes, *, fail_read: bool = False) -> None:
        self.payload = payload
        self.fail_read = fail_read
        self.offset = 0
        self.status = 200
        self.headers = {"Content-Length": str(len(payload))}

    def __enter__(self):
        return self

    def __exit__(self, exc_type, exc, traceback):
        return False

    def geturl(self):
        return "https://www.sec.gov/Archives/retry.txt"

    def read(self, maximum: int) -> bytes:
        if self.fail_read:
            raise http.client.IncompleteRead(b"partial", 100)
        chunk = self.payload[self.offset : self.offset + maximum]
        self.offset += len(chunk)
        return chunk


class _SequenceOpener:
    def __init__(self, responses: list[_TransportResponse]) -> None:
        self.responses = responses
        self.calls = 0

    def open(self, request, *, timeout):
        response = self.responses[self.calls]
        self.calls += 1
        return response


def test_sec_client_retries_incomplete_response_read():
    opener = _SequenceOpener(
        [
            _TransportResponse(b"unused", fail_read=True),
            _TransportResponse(b"complete"),
        ]
    )
    sleeps = []
    client = SecArchiveClient(
        "QuantMarketAI valid@example.org",
        retries=1,
        opener=opener,
        sleep_fn=sleeps.append,
        monotonic_fn=lambda: 0.0,
    )

    response = client.get_bytes(
        "https://www.sec.gov/Archives/retry.txt",
        max_bytes=100,
    )

    assert response.payload == b"complete"
    assert opener.calls == 2
    assert sleeps


def test_fatal_run_is_finalized_before_error_is_reraised(tmp_path):
    db, _ = _create_database(tmp_path)

    def broken_selector(*args, **kwargs):
        raise RuntimeError("selector failed")

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(RuntimeError, match="selector failed"):
            execute_ingestion_run(
                conn,
                ContentAddressedRawStore(tmp_path / "raw"),
                FakeClient({}, "2026-08-25T01:00"),
                accessions=[],
                max_filings=1,
                max_files_per_filing=1,
                max_file_bytes=1_000,
                max_index_bytes=1_000,
                max_total_bytes=10_000,
                run_id="fatal-run",
                select_fn=broken_selector,
            )
        run = conn.execute(
            """
            SELECT status, finished_at, error_count
            FROM source_ingestion_runs
            WHERE run_id = 'fatal-run'
            """
        ).fetchone()

    assert run[0] == "failed"
    assert run[1] is not None
    assert run[2] == 1


def test_migration_012_is_atomic_on_sql_failure(tmp_path, monkeypatch):
    db = _create_legacy_012_database(tmp_path, "atomic.db")
    invalid_migration = tmp_path / "invalid_012.sql"
    invalid_migration.write_text(
        "CREATE TABLE atomic_probe(value TEXT);\n"
        "THIS IS NOT VALID SQL;\n",
        encoding="utf-8",
    )
    monkeypatch.setattr(migration_012, "MIGRATION", invalid_migration)

    with pytest.raises(sqlite3.Error):
        migration_012.apply(db)

    with sqlite3.connect(db) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(sec_filing_files)")
        }
        atomic_probe = conn.execute(
            """
            SELECT 1 FROM sqlite_master
            WHERE type = 'table' AND name = 'atomic_probe'
            """
        ).fetchone()
        migration_row = conn.execute(
            "SELECT name FROM schema_migrations WHERE version = '012'"
        ).fetchone()
        legacy_note = conn.execute(
            "SELECT legacy_note FROM sec_filing_files"
        ).fetchone()[0]

    assert "download_status" not in columns
    assert atomic_probe is None
    assert migration_row is None
    assert legacy_note == "must-survive"


def test_migration_012_rejects_version_collision_without_mutation(tmp_path):
    db = _create_legacy_012_database(tmp_path, "collision.db")
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO schema_migrations(version, name)
            VALUES ('012', 'different_migration')
            """
        )

    with pytest.raises(RuntimeError, match="Colisión"):
        apply_012(db)

    with sqlite3.connect(db) as conn:
        columns = {
            row[1] for row in conn.execute("PRAGMA table_info(sec_filing_files)")
        }
    assert "download_status" not in columns


def test_migrations_preserve_legacy_schema_and_upgrade_012_history(tmp_path):
    db = _create_legacy_012_database(tmp_path, "legacy.db")
    apply_012(db)

    with sqlite3.connect(db) as conn:
        conn.execute(
            "INSERT INTO raw_source_documents VALUES ('inventory-raw')"
        )
        conn.execute(
            "INSERT INTO source_ingestion_runs VALUES ('legacy-run')"
        )
        conn.execute(
            """
            UPDATE sec_filing_files
            SET inventory_raw_document_id = 'inventory-raw'
            """
        )
        conn.execute(
            """
            INSERT INTO sec_filing_inventory_snapshots(
                filing_raw_document_id,
                inventory_raw_document_id,
                observed_at,
                parser_version,
                file_count,
                retrieval_run_id
            )
            VALUES (
                'filing-raw',
                'inventory-raw',
                '2026-08-24T10:00:00+00:00',
                'legacy-parser',
                1,
                'legacy-run'
            )
            """
        )
        conn.execute(
            """
            INSERT INTO sec_filing_file_versions(
                filing_raw_document_id,
                sequence_number,
                document_name,
                raw_document_id,
                observed_at,
                retrieval_run_id,
                version_status
            )
            VALUES (
                'filing-raw',
                '1',
                'legacy.htm',
                'document-raw',
                '2026-08-24T10:00:01+00:00',
                'legacy-run',
                'canonical'
            )
            """
        )
    apply_014(db)
    apply_014(db)

    with sqlite3.connect(db) as conn:
        legacy_row = conn.execute(
            "SELECT cache_key, payload FROM legacy_feature_cache"
        ).fetchone()
        filing_row = conn.execute(
            """
            SELECT legacy_note, download_status, inventory_status
            FROM sec_filing_files
            """
        ).fetchone()
        inventory_observations = conn.execute(
            "SELECT COUNT(*) FROM sec_filing_inventory_observations"
        ).fetchone()[0]
        file_observations = conn.execute(
            """
            SELECT observation_status
            FROM sec_filing_file_observations
            """
        ).fetchall()
        migrations = conn.execute(
            """
            SELECT version, name
            FROM schema_migrations
            ORDER BY version
            """
        ).fetchall()
        fk_errors = conn.execute("PRAGMA foreign_key_check").fetchall()

    assert legacy_row == ("alpha", "untouched")
    assert filing_row == ("must-survive", "pending", "unknown_migrated")
    assert inventory_observations == 1
    assert file_observations == [("canonical_first_seen",)]
    assert migrations == [
        ("012", "sec_filing_documents"),
        ("014", "sec_filing_observations"),
    ]
    assert fk_errors == []

    filing = FilingRecord(
        raw_document_id="filing-raw",
        cik="320193",
        accession_number=ACCESSION,
        form="8-K",
        acceptance_datetime=ACCEPTANCE,
        primary_document="legacy.htm",
        primary_doc_description="legacy",
    )
    item = InventoryFile(
        sequence_number="1",
        document_name="legacy.htm",
        document_type="8-K",
        description="legacy",
        declared_size_bytes=10,
        table_section="Document Format Files",
        is_primary=True,
    )
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _upsert_inventory_file(
            conn,
            filing=filing,
            item=item,
            inventory_raw_document_id="inventory-raw",
            observed_at="2026-08-25T10:00:00+00:00",
        )
        current = conn.execute(
            "SELECT inventory_status FROM sec_filing_files"
        ).fetchone()[0]
        _supersede_absent_inventory_rows(
            conn,
            filing=filing,
            current_inventory=[],
        )
        superseded = conn.execute(
            "SELECT inventory_status FROM sec_filing_files"
        ).fetchone()[0]
        with pytest.raises(sqlite3.IntegrityError):
            conn.execute(
                """
                UPDATE sec_filing_files
                SET inventory_status = 'invalid'
                """
            )

    assert current == "current"
    assert superseded == "superseded"


def test_index_parser_rejects_block_pages_truncation_and_empty_tables():
    blocked = b"<html><body><h1>Your Request Originates from an Undeclared Automated Tool</h1></body></html>"
    last_close = INDEX_HTML.rfind(b"</table>")
    truncated = INDEX_HTML[:last_close] + INDEX_HTML[last_close + len(b"</table>") :]
    empty = b"""
        <html><body>
        <table class="tableFile" summary="Document Format Files">
        <tr><th>Seq</th><th>Description</th><th>Document</th><th>Type</th></tr>
        </table>
        </body></html>
    """

    with pytest.raises(SecArchiveError, match="tableFile completa"):
        parse_filing_index(blocked)
    with pytest.raises(SecArchiveError, match="tableFile completa"):
        parse_filing_index(truncated)
    with pytest.raises(SecArchiveError, match="filas válidas"):
        parse_filing_index(empty)


def test_index_parser_preserves_nested_xsl_path_canonicalization():
    nested_name = "xslF345X06/form4.xml"
    href = (
        "/Archives/edgar/data/320193/000032019326000018/"
        f"{nested_name}"
    )
    row = (
        f'<tr><td>4</td><td>OWNERSHIP</td><td><a href="{href}">'
        "form4.xml</a></td><td>XML</td><td>42</td></tr>"
    ).encode()
    html = INDEX_HTML.replace(b"</table>", row + b"</table>", 1)

    inventory = parse_filing_index(html)
    nested = next(item for item in inventory if item.sequence_number == "4")

    assert nested.document_name == nested_name
    assert archive_file_url("320193", ACCESSION, nested.document_name).endswith(
        f"/{nested_name}"
    )


def test_metadata_primary_mismatch_does_not_supersede_prior_inventory(tmp_path):
    db, filing = _create_database(tmp_path)
    store = ContentAddressedRawStore(tmp_path / "raw")
    primary_row = (
        f'<tr><td>1</td><td>CURRENT REPORT</td><td><a href="{PRIMARY_NAME}">'
        f"{PRIMARY_NAME}</a></td><td>8-K</td><td>120</td></tr>"
    ).encode()
    mismatched_index = INDEX_HTML.replace(primary_row, b"")

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _insert_run(conn, "fallback-full")
        process_filing(
            conn,
            store,
            FakeClient(_payloads(), "2026-08-25T11:00"),
            filing,
            run_id="fallback-full",
            budget=ByteBudget(1_000_000),
            max_files=10,
            max_file_bytes=10_000,
            max_index_bytes=100_000,
        )
        conn.commit()

        payloads = _payloads()
        payloads[archive_index_url("320193", ACCESSION)] = mismatched_index
        _insert_run(conn, "fallback-mismatch")
        process_filing(
            conn,
            store,
            FakeClient(payloads, "2026-08-25T12:00"),
            filing,
            run_id="fallback-mismatch",
            budget=ByteBudget(1_000_000),
            max_files=10,
            max_file_bytes=10_000,
            max_index_bytes=100_000,
        )
        conn.commit()
        primary_rows = conn.execute(
            """
            SELECT sequence_number, inventory_status
            FROM sec_filing_files
            WHERE document_name = ?
            ORDER BY sequence_number
            """,
            (PRIMARY_NAME,),
        ).fetchall()

    assert primary_rows == [
        ("1", "current"),
        ("__metadata_primary__", "current"),
    ]


class _FailingPrimaryClient(FakeClient):
    def __init__(
        self,
        payloads: dict[str, bytes],
        timestamp_prefix: str,
        conn: sqlite3.Connection,
    ) -> None:
        super().__init__(payloads, timestamp_prefix)
        self.conn = conn
        self.status_seen_during_fetch = None

    def get_bytes(self, url: str, *, max_bytes: int) -> SecResponse:
        if url == archive_file_url("320193", ACCESSION, PRIMARY_NAME):
            self.status_seen_during_fetch = self.conn.execute(
                """
                SELECT download_status
                FROM sec_filing_files
                WHERE document_name = ? AND sequence_number = '1'
                """,
                (PRIMARY_NAME,),
            ).fetchone()[0]
            raise SecArchiveError("controlled refetch failure")
        return super().get_bytes(url, max_bytes=max_bytes)


def test_failed_refetch_preserves_success_and_records_attempt_run(tmp_path):
    db, filing = _create_database(tmp_path)
    store = ContentAddressedRawStore(tmp_path / "raw")

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _insert_run(conn, "refetch-success")
        process_filing(
            conn,
            store,
            FakeClient(_payloads(), "2026-08-25T13:00"),
            filing,
            run_id="refetch-success",
            budget=ByteBudget(1_000_000),
            max_files=10,
            max_file_bytes=10_000,
            max_index_bytes=100_000,
        )
        conn.commit()
        before = conn.execute(
            """
            SELECT raw_document_id, download_status, downloaded_at
            FROM sec_filing_files
            WHERE document_name = ? AND sequence_number = '1'
            """,
            (PRIMARY_NAME,),
        ).fetchone()

        _insert_run(conn, "refetch-failed")
        client = _FailingPrimaryClient(
            _payloads(),
            "2026-08-25T14:00",
            conn,
        )
        failed = process_filing(
            conn,
            store,
            client,
            filing,
            run_id="refetch-failed",
            budget=ByteBudget(1_000_000),
            max_files=10,
            max_file_bytes=10_000,
            max_index_bytes=100_000,
        )
        conn.commit()
        after = conn.execute(
            """
            SELECT raw_document_id, download_status, downloaded_at,
                   last_attempted_at, selection_reason, error_json,
                   last_attempt_run_id
            FROM sec_filing_files
            WHERE document_name = ? AND sequence_number = '1'
            """,
            (PRIMARY_NAME,),
        ).fetchone()

    assert client.status_seen_during_fetch == "downloaded"
    assert after[:3] == before
    assert after[3] is not None
    assert after[4] == "primary"
    assert "controlled refetch failure" in after[5]
    assert after[6] == "refetch-failed"
    assert len(failed.errors) == 1


class _OutcomeOpener:
    def __init__(self, outcomes: list[object]) -> None:
        self.outcomes = outcomes
        self.calls = 0

    def open(self, request, *, timeout):
        outcome = self.outcomes[self.calls]
        self.calls += 1
        if isinstance(outcome, BaseException):
            raise outcome
        return outcome


def _retry_after_error(seconds: str) -> urllib.error.HTTPError:
    return urllib.error.HTTPError(
        "https://www.sec.gov/Archives/retry.txt",
        429,
        "Too Many Requests",
        {"Retry-After": seconds},
        None,
    )


def test_retry_after_is_respected_or_run_is_postponed():
    sleeps = []
    allowed_opener = _OutcomeOpener(
        [
            _retry_after_error("3"),
            _TransportResponse(b"complete"),
        ]
    )
    allowed_client = SecArchiveClient(
        "QuantMarketAI valid@example.org",
        retries=1,
        max_retry_after_seconds=5,
        opener=allowed_opener,
        sleep_fn=sleeps.append,
        monotonic_fn=lambda: 0.0,
    )

    response = allowed_client.get_bytes(
        "https://www.sec.gov/Archives/retry.txt",
        max_bytes=100,
    )

    assert response.payload == b"complete"
    assert sleeps[0] == 3.0

    postponed_opener = _OutcomeOpener(
        [
            _retry_after_error("120"),
            _TransportResponse(b"must-not-fetch"),
        ]
    )
    postponed_client = SecArchiveClient(
        "QuantMarketAI valid@example.org",
        retries=1,
        max_retry_after_seconds=5,
        opener=postponed_opener,
        sleep_fn=lambda seconds: None,
    )

    with pytest.raises(SecArchiveError, match="posponerse"):
        postponed_client.get_bytes(
            "https://www.sec.gov/Archives/retry.txt",
            max_bytes=100,
        )
    assert postponed_opener.calls == 1


def test_invalid_gzip_is_normalized_to_sec_archive_error():
    with pytest.raises(SecArchiveError, match="gzip inválida"):
        _decode_gzip_limited(b"not-a-gzip-stream", 1_000)


def test_sqlite_failure_is_not_downgraded_to_download_error(
    tmp_path,
    monkeypatch,
):
    db, filing = _create_database(tmp_path)

    def broken_link(*args, **kwargs):
        raise sqlite3.OperationalError("database programming failure")

    monkeypatch.setattr(sec_documents, "_link_file_version", broken_link)

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _insert_run(conn, "sqlite-failure")
        with pytest.raises(
            sqlite3.OperationalError,
            match="database programming failure",
        ):
            process_filing(
                conn,
                ContentAddressedRawStore(tmp_path / "raw"),
                FakeClient(_payloads(), "2026-08-25T15:00"),
                filing,
                run_id="sqlite-failure",
                budget=ByteBudget(1_000_000),
                max_files=10,
                max_file_bytes=10_000,
                max_index_bytes=100_000,
            )
        conn.rollback()

class _TransactionInspectingClient(FakeClient):
    def __init__(
        self,
        payloads: dict[str, bytes],
        timestamp_prefix: str,
        conn: sqlite3.Connection,
    ) -> None:
        super().__init__(payloads, timestamp_prefix)
        self.conn = conn
        self.transaction_states: list[tuple[str, bool]] = []

    def get_bytes(self, url: str, *, max_bytes: int) -> SecResponse:
        self.transaction_states.append((url, self.conn.in_transaction))
        if self.conn.in_transaction:
            raise AssertionError("network fetch executed inside SQLite transaction")
        return super().get_bytes(url, max_bytes=max_bytes)


def test_index_and_selected_file_fetches_run_without_sqlite_transaction(
    tmp_path,
):
    db, filing = _create_database(tmp_path)

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _insert_run(conn, "two-phase-fetch")
        client = _TransactionInspectingClient(
            _payloads(),
            "2026-08-25T16:00",
            conn,
        )

        stats = process_filing(
            conn,
            ContentAddressedRawStore(tmp_path / "raw"),
            client,
            filing,
            run_id="two-phase-fetch",
            budget=ByteBudget(1_000_000),
            max_files=10,
            max_file_bytes=10_000,
            max_index_bytes=100_000,
        )

        assert conn.in_transaction is False

    assert stats.files_downloaded == 2
    assert [state for _, state in client.transaction_states] == [
        False,
        False,
        False,
    ]
    assert client.transaction_states[0][0] == archive_index_url(
        "320193",
        ACCESSION,
    )


def test_execute_propagates_sqlite_failure_and_persists_failed_run(
    tmp_path,
    monkeypatch,
):
    db, _ = _create_database(tmp_path)

    def broken_link(*args, **kwargs):
        raise sqlite3.OperationalError("execute sqlite failure")

    monkeypatch.setattr(sec_documents, "_link_file_version", broken_link)

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        with pytest.raises(
            sqlite3.OperationalError,
            match="execute sqlite failure",
        ) as raised:
            execute_ingestion_run(
                conn,
                ContentAddressedRawStore(tmp_path / "raw"),
                FakeClient(_payloads(), "2026-08-25T17:00"),
                accessions=[ACCESSION],
                max_filings=1,
                max_files_per_filing=10,
                max_file_bytes=10_000,
                max_index_bytes=100_000,
                max_total_bytes=1_000_000,
                run_id="execute-sqlite-failure",
            )
        run = conn.execute(
            """
            SELECT status, finished_at, error_count, error_json
            FROM source_ingestion_runs
            WHERE run_id = 'execute-sqlite-failure'
            """
        ).fetchone()
        inventory_observations = conn.execute(
            "SELECT COUNT(*) FROM sec_filing_inventory_observations"
        ).fetchone()[0]

    assert run[0] == "failed"
    assert run[1] is not None
    assert run[2] == 1
    assert "execute sqlite failure" in run[3]
    assert inventory_observations == 0
    assert raised.value.sec_ingestion_result["status"] == "failed"


class _ExpectedIndexFailureClient:
    def __init__(self) -> None:
        self.calls = 0

    def get_bytes(self, url: str, *, max_bytes: int) -> SecResponse:
        self.calls += 1
        raise SecArchiveError("temporary SEC archive outage")


def test_expected_sec_error_remains_auditable_completed_with_errors(tmp_path):
    db, _ = _create_database(tmp_path)
    client = _ExpectedIndexFailureClient()

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        result = execute_ingestion_run(
            conn,
            ContentAddressedRawStore(tmp_path / "raw"),
            client,
            accessions=[ACCESSION],
            max_filings=1,
            max_files_per_filing=10,
            max_file_bytes=10_000,
            max_index_bytes=100_000,
            max_total_bytes=1_000_000,
            run_id="expected-sec-error",
        )
        run = conn.execute(
            """
            SELECT status, finished_at, error_count, error_json
            FROM source_ingestion_runs
            WHERE run_id = 'expected-sec-error'
            """
        ).fetchone()

    assert client.calls == 1
    assert result["status"] == "completed_with_errors"
    assert result["errors"][0]["accession_number"] == ACCESSION
    assert run[0] == "completed_with_errors"
    assert run[1] is not None
    assert run[2] == 1
    assert "temporary SEC archive outage" in run[3]


def test_cli_keeps_json_and_returns_nonzero_for_non_success(
    tmp_path,
    monkeypatch,
    capsys,
):
    argv = [
        "sec_filing_documents.py",
        "--db",
        str(tmp_path / "cli.db"),
        "--raw-root",
        str(tmp_path / "raw"),
        "--user-agent",
        "QuantMarketAI valid@example.org",
    ]
    monkeypatch.setattr(sys, "argv", argv)
    completed_with_errors = {
        "run_id": "cli-partial",
        "status": "completed_with_errors",
        "errors": [{"error": "expected SEC failure"}],
    }
    monkeypatch.setattr(
        sec_documents,
        "execute_ingestion_run",
        lambda *args, **kwargs: completed_with_errors,
    )

    assert sec_documents.main() == 1
    first_output = json.loads(capsys.readouterr().out)
    assert first_output == completed_with_errors

    def fatal_execute(*args, **kwargs):
        raise sqlite3.OperationalError("unexpected CLI database failure")

    monkeypatch.setattr(sec_documents, "execute_ingestion_run", fatal_execute)

    assert sec_documents.main() == 1
    second_output = json.loads(capsys.readouterr().out)
    assert second_output["status"] == "failed"
    assert "unexpected CLI database failure" in second_output["errors"][0]["error"]

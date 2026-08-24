import gzip
import hashlib
import json
import sqlite3
from pathlib import Path

import pytest

import ingestion.events.deterministic_clustering as clustering_module
from database.apply_migration_011 import apply as apply_011
from database.apply_migration_012 import apply as apply_012
from database.apply_migration_014 import apply as apply_014
from database.apply_migration_015 import apply as apply_015
from ingestion.events.deterministic_clustering import (
    ClusteringConfig,
    EvidenceDocument,
    build_parser,
    cluster_documents,
    run_clustering,
)


ACCESSION = "0000000001-26-000001"
ACCEPTANCE = "2026-08-20T10:00:00+00:00"


def _create_db(tmp_path: Path, name: str = "cluster.db") -> Path:
    db = tmp_path / name
    schema = Path("database/schema.sql").read_text(encoding="utf-8")
    event_layer = Path(
        "database/migrations/010_event_layer.sql"
    ).read_text(encoding="utf-8")
    with sqlite3.connect(db) as conn:
        conn.executescript(schema)
        conn.executescript(event_layer)
    apply_011(db)
    apply_012(db)
    apply_014(db)
    apply_015(db)
    apply_015(db)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO assets(asset_id, ticker, name)
            VALUES (1, 'ACME', 'Acme Corporation')
            """
        )
    return db


def _insert_news(
    db: Path,
    news_id: str,
    available_at: str,
    text: str,
    *,
    asset_id: int = 1,
) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO news_documents(
                news_id,
                published_at,
                ingested_at,
                source_name,
                source_provider,
                title,
                raw_text
            )
            VALUES (?, ?, ?, 'Legacy Wire', 'legacy_import', ?, ?)
            """,
            (
                news_id,
                available_at,
                "2026-08-24T12:00:00+00:00",
                text,
                text,
            ),
        )
        conn.execute(
            """
            INSERT INTO news_assets(news_id, asset_id, role)
            VALUES (?, ?, 'subject')
            """,
            (news_id, asset_id),
        )


def _assignments_without_run_specific_ids(result: dict) -> list[tuple]:
    return [
        (
            row["evidence_key"],
            row["cluster_id"],
            row["match_method"],
            row["matched_evidence_key"],
            row["decision_order"],
        )
        for row in result["assignments"]
    ]


def _write_gzip(path: Path, payload: bytes) -> tuple[str, int]:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("wb") as raw_stream:
        with gzip.GzipFile(fileobj=raw_stream, mode="wb", mtime=0) as stream:
            stream.write(payload)
    return hashlib.sha256(payload).hexdigest(), len(payload)


def _insert_raw(
    conn: sqlite3.Connection,
    *,
    raw_document_id: str,
    external_id: str,
    available_at: str,
    payload: bytes,
    path: Path,
    parent_raw_document_id: str | None,
    document_kind: str,
) -> None:
    digest, length = _write_gzip(path, payload)
    conn.execute(
        """
        INSERT INTO raw_source_documents(
            raw_document_id,
            source_id,
            external_id,
            document_kind,
            source_url,
            available_at,
            retrieved_at,
            content_type,
            content_encoding,
            raw_sha256,
            storage_path,
            byte_length,
            parent_raw_document_id
        )
        VALUES (
            ?, 'sec_edgar', ?, ?, 'https://www.sec.gov/test',
            ?, ?, 'text/html', 'gzip', ?, ?, ?, ?
        )
        """,
        (
            raw_document_id,
            external_id,
            document_kind,
            available_at,
            available_at,
            digest,
            str(path),
            length,
            parent_raw_document_id,
        ),
    )


def _insert_sec_a_b_a(db: Path, tmp_path: Path) -> None:
    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys = ON")
        _insert_raw(
            conn,
            raw_document_id="filing-meta",
            external_id=f"filing/{ACCESSION}",
            available_at=ACCEPTANCE,
            payload=b'{"form":"8-K"}',
            path=tmp_path / "raw" / "meta.json.gz",
            parent_raw_document_id=None,
            document_kind="sec_filing_metadata",
        )
        conn.execute(
            """
            INSERT INTO sec_filings(
                raw_document_id,
                cik,
                accession_number,
                form,
                acceptance_datetime,
                primary_document,
                entity_name,
                ticker_at_ingestion,
                metadata_version
            )
            VALUES (
                'filing-meta', '1', ?, '8-K', ?, 'report.htm',
                'Acme Corporation', 'ACME', 'sec_submission_v2'
            )
            """,
            (ACCESSION, ACCEPTANCE),
        )
        conn.execute(
            """
            INSERT INTO raw_document_assets(
                raw_document_id,
                asset_id,
                role,
                linking_method,
                linking_version
            )
            VALUES ('filing-meta', 1, 'issuer', 'ticker_cik', 'test_v1')
            """
        )
        _insert_raw(
            conn,
            raw_document_id="raw-a",
            external_id=f"archive/{ACCESSION}/report.htm/a",
            available_at=ACCEPTANCE,
            payload=b"<html><body>Acme files current report A</body></html>",
            path=tmp_path / "raw" / "a.html.gz",
            parent_raw_document_id="filing-meta",
            document_kind="sec_filing_file",
        )
        _insert_raw(
            conn,
            raw_document_id="raw-b",
            external_id=f"archive/{ACCESSION}/report.htm/b",
            available_at="2026-08-20T12:00:00+00:00",
            payload=b"<html><body>Corrected and different report B</body></html>",
            path=tmp_path / "raw" / "b.html.gz",
            parent_raw_document_id="filing-meta",
            document_kind="sec_filing_file",
        )
        conn.execute(
            """
            INSERT INTO sec_filing_files(
                filing_raw_document_id,
                sequence_number,
                document_name,
                document_type,
                description,
                source_url,
                is_primary,
                raw_document_id,
                download_status
            )
            VALUES (
                'filing-meta', '1', 'report.htm', '8-K',
                'CURRENT REPORT', 'https://www.sec.gov/report.htm',
                1, 'raw-a', 'downloaded'
            )
            """
        )
        conn.executemany(
            """
            INSERT INTO sec_filing_file_versions(
                filing_raw_document_id,
                sequence_number,
                document_name,
                raw_document_id,
                observed_at,
                version_status
            )
            VALUES ('filing-meta', '1', 'report.htm', ?, ?, ?)
            """,
            [
                ("raw-a", "2026-08-20T10:05:00+00:00", "canonical"),
                ("raw-b", "2026-08-20T12:00:00+00:00", "revision_observed"),
            ],
        )
        conn.executemany(
            """
            INSERT INTO sec_filing_file_observations(
                observation_id,
                filing_raw_document_id,
                sequence_number,
                document_name,
                raw_document_id,
                observed_at,
                observation_status
            )
            VALUES (?, 'filing-meta', '1', 'report.htm', ?, ?, ?)
            """,
            [
                (
                    "obs-a-first",
                    "raw-a",
                    "2026-08-20T10:05:00+00:00",
                    "canonical_first_seen",
                ),
                (
                    "obs-b",
                    "raw-b",
                    "2026-08-20T12:00:00+00:00",
                    "revision_first_seen",
                ),
                (
                    "obs-a-rerun",
                    "raw-a",
                    "2026-08-20T13:00:00+00:00",
                    "canonical_rerun",
                ),
            ],
        )


def test_migration_015_is_idempotent_and_additive(tmp_path):
    db = _create_db(tmp_path)

    with sqlite3.connect(db) as conn:
        tables = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'table'"
            )
        }
        assert {
            "event_clustering_configs",
            "event_clustering_runs",
            "event_document_fingerprints",
            "event_cluster_memberships",
            "event_cluster_news_membership_refs",
            "event_cluster_raw_membership_refs",
            "event_cluster_sec_observation_refs",
        } <= tables
        assert conn.execute(
            """
            SELECT name
            FROM schema_migrations
            WHERE version = '015'
            """
        ).fetchone() == ("deterministic_event_clustering",)
        assert conn.execute("PRAGMA foreign_key_check").fetchall() == []
        views = {
            row[0]
            for row in conn.execute(
                "SELECT name FROM sqlite_master WHERE type = 'view'"
            )
        }
        assert {
            "event_cluster_news_by_run",
            "event_clusters_by_run",
        } <= views
        conn.execute("DROP VIEW event_clusters_by_run")

    apply_015(db)
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            """
            SELECT 1
            FROM sqlite_master
            WHERE type = 'view'
              AND name = 'event_clusters_by_run'
            """
        ).fetchone() == (1,)


def test_determinism_exact_near_duplicate_and_no_overmerge(tmp_path):
    texts = {
        "n1": (
            "Acme announces a new solar battery factory in Texas "
            "with production starting next year"
        ),
        "n2": (
            "ACME ANNOUNCES A NEW SOLAR BATTERY FACTORY IN TEXAS "
            "WITH PRODUCTION STARTING NEXT YEAR!"
        ),
        "n3": (
            "Acme announces a new solar battery factory in Texas "
            "with production starting next year officially"
        ),
        "n4": (
            "Acme appoints a new independent director after its "
            "annual shareholder meeting"
        ),
    }
    times = {
        "n1": "2026-08-20T10:00:00+00:00",
        "n2": "2026-08-20T10:10:00+00:00",
        "n3": "2026-08-20T10:20:00+00:00",
        "n4": "2026-08-20T10:30:00+00:00",
    }
    first = _create_db(tmp_path, "first.db")
    second = _create_db(tmp_path, "second.db")
    for news_id in ("n4", "n2", "n1", "n3"):
        _insert_news(first, news_id, times[news_id], texts[news_id])
    for news_id in ("n1", "n3", "n4", "n2"):
        _insert_news(second, news_id, times[news_id], texts[news_id])

    one = run_clustering(
        first,
        source="news",
        ticker="ACME",
        run_id="determinism-one",
    )
    two = run_clustering(
        second,
        source="news",
        ticker="ACME",
        run_id="determinism-two",
    )

    assert _assignments_without_run_specific_ids(
        one
    ) == _assignments_without_run_specific_ids(two)
    by_id = {row["evidence_id"]: row for row in one["assignments"]}
    assert by_id["n1"]["match_method"] == "anchor"
    assert by_id["n2"]["match_method"] == "exact_text"
    assert by_id["n3"]["match_method"] == "near_duplicate"
    assert by_id["n1"]["cluster_id"] == by_id["n2"]["cluster_id"]
    assert by_id["n1"]["cluster_id"] == by_id["n3"]["cluster_id"]
    assert by_id["n4"]["match_method"] == "anchor"
    assert by_id["n4"]["cluster_id"] != by_id["n1"]["cluster_id"]

    with sqlite3.connect(first) as conn:
        legacy_rows = conn.execute(
            """
            SELECT availability_basis, availability_is_point_in_time
            FROM event_cluster_memberships
            ORDER BY decision_order
            """
        ).fetchall()
        assert legacy_rows
        assert all("assumed_not_pit_verified" in row[0] for row in legacy_rows)
        assert all(row[1] == 0 for row in legacy_rows)
        config_json = conn.execute(
            "SELECT configuration_json FROM event_clustering_configs"
        ).fetchone()[0]
        registered = json.loads(config_json)
        assert registered["near_duplicate_threshold"] == pytest.approx(0.82)
        assert registered["max_candidates_per_document"] == 128
        assert registered["min_exact_duplicate_tokens"] == 8
        cluster_metadata = json.loads(
            conn.execute(
                """
                SELECT metadata_json
                FROM event_clusters
                ORDER BY first_available_at
                LIMIT 1
                """
            ).fetchone()[0]
        )
        assert cluster_metadata["anchor_availability_is_point_in_time"] is False


def test_rerun_is_idempotent_and_new_run_is_append_only(tmp_path):
    db = _create_db(tmp_path)
    _insert_news(
        db,
        "n1",
        "2026-08-20T10:00:00+00:00",
        "Acme releases quarterly operational production update",
    )
    _insert_news(
        db,
        "n2",
        "2026-08-20T10:05:00+00:00",
        "Acme releases quarterly operational production update",
    )

    first = run_clustering(
        db,
        source="news",
        run_id="stable-run",
    )
    reused = run_clustering(
        db,
        source="news",
        run_id="stable-run",
    )
    second_run = run_clustering(
        db,
        source="news",
        run_id="append-only-run",
    )

    assert reused["rerun_reused"] is True
    assert _assignments_without_run_specific_ids(
        first
    ) == _assignments_without_run_specific_ids(reused)
    assert _assignments_without_run_specific_ids(
        first
    ) == _assignments_without_run_specific_ids(second_run)
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM event_clustering_runs"
        ).fetchone()[0] == 2
        assert conn.execute(
            "SELECT COUNT(*) FROM event_cluster_memberships"
        ).fetchone()[0] == 4
        assert conn.execute(
            "SELECT COUNT(*) FROM event_cluster_news"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM event_cluster_news_by_run"
        ).fetchone()[0] == 4


def test_future_evidence_never_changes_prior_assignments(tmp_path):
    db = _create_db(tmp_path)
    text = "Acme confirms a definitive supply agreement with Orion"
    _insert_news(db, "n1", "2026-08-20T10:00:00+00:00", text)
    _insert_news(db, "n2", "2026-08-20T11:00:00+00:00", text)

    early = run_clustering(
        db,
        source="news",
        end="2026-08-20T11:30:00+00:00",
        run_id="early",
    )
    cluster_id = early["assignments"][0]["cluster_id"]
    with sqlite3.connect(db) as conn:
        identity_after_early = conn.execute(
            """
            SELECT first_available_at, last_available_at
            FROM event_clusters
            WHERE cluster_id = ?
            """,
            (cluster_id,),
        ).fetchone()
    assert identity_after_early == (
        "2026-08-20T10:00:00+00:00",
        "2026-08-20T10:00:00+00:00",
    )

    _insert_news(db, "n3", "2026-08-20T15:00:00+00:00", text)
    later = run_clustering(
        db,
        source="news",
        end="2026-08-20T16:00:00+00:00",
        run_id="later",
    )

    early_prior = _assignments_without_run_specific_ids(early)
    later_prior = [
        row
        for row in _assignments_without_run_specific_ids(later)
        if row[0] in {"news:n1", "news:n2"}
    ]
    assert early_prior == later_prior
    with sqlite3.connect(db) as conn:
        violations = conn.execute(
            """
            SELECT COUNT(*)
            FROM event_cluster_memberships AS current
            JOIN event_cluster_memberships AS matched
              ON matched.membership_id = current.matched_membership_id
            WHERE matched.evidence_available_at > current.evidence_available_at
            """
        ).fetchone()[0]
        assert violations == 0
        assert conn.execute(
            """
            SELECT first_available_at, last_available_at
            FROM event_clusters
            WHERE cluster_id = ?
            """,
            (cluster_id,),
        ).fetchone() == identity_after_early
        assert conn.execute(
            """
            SELECT
                clustering_run_id,
                first_available_at,
                last_available_at,
                evidence_count
            FROM event_clusters_by_run
            WHERE cluster_id = ?
            ORDER BY clustering_run_id
            """,
            (cluster_id,),
        ).fetchall() == [
            (
                "early",
                "2026-08-20T10:00:00+00:00",
                "2026-08-20T11:00:00+00:00",
                2,
            ),
            (
                "later",
                "2026-08-20T10:00:00+00:00",
                "2026-08-20T15:00:00+00:00",
                3,
            ),
        ]
        assert conn.execute(
            """
            SELECT clustering_run_id, COUNT(*)
            FROM event_cluster_news_by_run
            WHERE cluster_id = ?
            GROUP BY clustering_run_id
            ORDER BY clustering_run_id
            """,
            (cluster_id,),
        ).fetchall() == [
            ("early", 2),
            ("later", 3),
        ]


def test_sec_accession_and_a_b_a_observations_are_preserved(tmp_path):
    db = _create_db(tmp_path)
    _insert_sec_a_b_a(db, tmp_path)

    result = run_clustering(
        db,
        source="sec",
        ticker="ACME",
        end="2026-08-20T13:30:00+00:00",
        run_id="sec-a-b-a",
    )

    assert result["documents_considered"] == 2
    assert result["cluster_count"] == 1
    assert [row["match_method"] for row in result["assignments"]] == [
        "anchor",
        "sec_accession_provenance",
    ]
    with sqlite3.connect(db) as conn:
        memberships = conn.execute(
            """
            SELECT
                membership.evidence_id,
                membership.availability_basis,
                membership.availability_is_point_in_time,
                COUNT(observation.observation_id)
            FROM event_cluster_memberships AS membership
            LEFT JOIN event_cluster_sec_observation_refs AS observation
              ON observation.membership_id = membership.membership_id
            WHERE membership.clustering_run_id = 'sec-a-b-a'
            GROUP BY membership.membership_id
            ORDER BY membership.evidence_id
            """
        ).fetchall()
        assert memberships == [
            ("raw-a", "sec_acceptance_datetime", 1, 2),
            ("raw-b", "sec_revision_retrieval_available_at", 1, 1),
        ]
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM event_cluster_sec_observation_refs
            """
        ).fetchone()[0] == 3
        cluster_metadata = json.loads(
            conn.execute(
                """
                SELECT metadata_json
                FROM event_clusters
                WHERE cluster_id = ?
                """,
                (result["assignments"][0]["cluster_id"],),
            ).fetchone()[0]
        )
        assert cluster_metadata["anchor_availability_is_point_in_time"] is True
        assert (
            cluster_metadata["anchor_availability_basis"]
            == "sec_acceptance_datetime"
        )


def test_blocking_caps_near_duplicate_comparisons():
    config = ClusteringConfig(
        near_duplicate_threshold=0.50,
        max_candidates_per_document=7,
    )
    documents = [
        EvidenceDocument(
            evidence_type="news_document",
            evidence_id=f"n{index:03d}",
            available_at=f"2026-08-20T10:{index // 60:02d}:{index % 60:02d}+00:00",
            availability_basis="legacy_assumed_not_pit_verified",
            availability_is_point_in_time=False,
            title=None,
            text=(
                "common company announcement about a factory project "
                f"with deterministic suffix {index}"
            ),
            content_sha256=hashlib.sha256(str(index).encode()).hexdigest(),
            source_name="test",
            source_id="test",
            asset_ids=(1,),
        )
        for index in range(120)
    ]

    assignments, comparisons = cluster_documents(
        list(reversed(documents)),
        config,
        run_id="blocking",
    )

    assert len(assignments) == 120
    assert comparisons <= 120 * config.max_candidates_per_document
    assert [row.decision_order for row in assignments] == list(range(120))


def test_cli_defaults_to_sec():
    assert build_parser().parse_args([]).source == "sec"


def test_dry_run_writes_nothing(tmp_path):
    db = _create_db(tmp_path)
    _insert_news(
        db,
        "n1",
        "2026-08-20T10:00:00+00:00",
        "Acme announces a bounded dry run test document",
    )

    result = run_clustering(
        db,
        source="news",
        run_id="dry",
        dry_run=True,
    )

    assert result["status"] == "dry_run"
    assert result["writes"] == 0
    with sqlite3.connect(db) as conn:
        assert conn.execute(
            "SELECT COUNT(*) FROM event_clustering_runs"
        ).fetchone()[0] == 0
        assert conn.execute(
            "SELECT COUNT(*) FROM event_document_fingerprints"
        ).fetchone()[0] == 0


def test_utc_offsets_are_normalized_before_range_sort_and_top_k(tmp_path):
    db = _create_db(tmp_path)
    _insert_news(
        db,
        "late-by-offset",
        "2026-08-20T08:00:00-04:00",
        "Acme publishes a sufficiently detailed late document for testing",
    )
    _insert_news(
        db,
        "early-utc",
        "2026-08-20T11:00:00+00:00",
        "Acme publishes a sufficiently detailed early document for testing",
    )
    _insert_news(
        db,
        "outside-range",
        "2026-08-20T09:30:00-03:00",
        "Acme publishes another detailed document outside the selected range",
    )

    result = run_clustering(
        db,
        source="news",
        start="2026-08-20T10:30:00+00:00",
        end="2026-08-20T11:30:00+00:00",
        max_documents=1,
        run_id="offset-top-k",
    )

    assert [row["evidence_id"] for row in result["assignments"]] == [
        "early-utc"
    ]
    assert result["assignments"][0]["available_at"] == (
        "2026-08-20T11:00:00+00:00"
    )
    assert all(
        "2026-08-20T10:30:00+00:00"
        <= row["available_at"]
        <= "2026-08-20T11:30:00+00:00"
        for row in result["assignments"]
    )
    with sqlite3.connect(db) as conn:
        selection = json.loads(
            conn.execute(
                """
                SELECT selection_json
                FROM event_clustering_runs
                WHERE clustering_run_id = 'offset-top-k'
                """
            ).fetchone()[0]
        )
        assert selection["reconstruction_non_pit"] is True
        assert "not_pit_verified" in selection["temporal_contract"]


def test_exact_text_requires_minimum_tokens_and_asset_overlap():
    config = ClusteringConfig(
        min_exact_duplicate_tokens=8,
        near_duplicate_threshold=1.0,
    )

    def document(
        evidence_id: str,
        available_at: str,
        text: str,
        assets: tuple[int, ...],
        accession: str | None = None,
    ) -> EvidenceDocument:
        return EvidenceDocument(
            evidence_type="news_document",
            evidence_id=evidence_id,
            available_at=available_at,
            availability_basis="test",
            availability_is_point_in_time=True,
            title=None,
            text=text,
            content_sha256=hashlib.sha256(text.encode()).hexdigest(),
            source_name="test",
            source_id="test",
            asset_ids=assets,
            sec_accession_number=accession,
        )

    long_text = (
        "Acme confirms a definitive long term supply agreement "
        "for advanced battery materials"
    )
    documents = [
        document("short-a", "2026-08-20T10:00:00+00:00", "brief update", (1,)),
        document("short-b", "2026-08-20T10:01:00+00:00", "brief update", (1,)),
        document("long-a", "2026-08-20T10:02:00+00:00", long_text, (1,)),
        document("long-other", "2026-08-20T10:03:00+00:00", long_text, (2,)),
        document("long-same", "2026-08-20T10:04:00+00:00", long_text, (1,)),
        document(
            "sec-short-a",
            "2026-08-20T10:05:00+00:00",
            "x",
            (),
            "0001-26-000001",
        ),
        document(
            "sec-short-b",
            "2026-08-20T10:06:00+00:00",
            "y",
            (),
            "0001-26-000001",
        ),
    ]

    assignments, _ = cluster_documents(
        documents,
        config,
        run_id="exact-guardrails",
    )
    by_id = {row.evidence_id: row for row in assignments}

    assert by_id["short-a"].cluster_id != by_id["short-b"].cluster_id
    assert by_id["long-a"].cluster_id != by_id["long-other"].cluster_id
    assert by_id["long-same"].match_method == "exact_text"
    assert by_id["long-same"].cluster_id == by_id["long-a"].cluster_id
    assert by_id["sec-short-b"].match_method == "sec_accession_provenance"
    assert by_id["sec-short-b"].cluster_id == by_id["sec-short-a"].cluster_id


def test_sec_observation_offsets_are_filtered_in_python(tmp_path):
    db = _create_db(tmp_path)
    _insert_sec_a_b_a(db, tmp_path)
    with sqlite3.connect(db) as conn:
        conn.execute(
            """
            INSERT INTO sec_filing_file_observations(
                observation_id,
                filing_raw_document_id,
                sequence_number,
                document_name,
                raw_document_id,
                observed_at,
                observation_status
            )
            VALUES (
                'obs-a-offset-future',
                'filing-meta',
                '1',
                'report.htm',
                'raw-a',
                '2026-08-20T09:00:00-03:00',
                'canonical_rerun'
            )
            """
        )

    result = run_clustering(
        db,
        source="sec",
        end="2026-08-20T11:30:00+00:00",
        run_id="observation-offset",
    )

    assert [row["evidence_id"] for row in result["assignments"]] == ["raw-a"]
    with sqlite3.connect(db) as conn:
        observations = conn.execute(
            """
            SELECT observation_id
            FROM event_cluster_sec_observation_refs AS observation
            JOIN event_cluster_memberships AS membership
              ON membership.membership_id = observation.membership_id
            WHERE membership.clustering_run_id = 'observation-offset'
            ORDER BY observation_id
            """
        ).fetchall()
        assert observations == [("obs-a-first",)]


def test_corrupt_unselected_gzip_is_not_read_and_failed_run_is_audited(
    tmp_path,
):
    db = _create_db(tmp_path)
    _insert_sec_a_b_a(db, tmp_path)
    (tmp_path / "raw" / "b.html.gz").write_bytes(b"not-a-gzip")

    bounded = run_clustering(
        db,
        source="sec",
        max_documents=1,
        run_id="corrupt-outside-limit",
    )
    assert [row["evidence_id"] for row in bounded["assignments"]] == ["raw-a"]

    with pytest.raises((gzip.BadGzipFile, OSError, EOFError)):
        run_clustering(
            db,
            source="sec",
            max_documents=2,
            run_id="corrupt-selected",
        )

    with sqlite3.connect(db) as conn:
        status, error_json = conn.execute(
            """
            SELECT status, error_json
            FROM event_clustering_runs
            WHERE clustering_run_id = 'corrupt-selected'
            """
        ).fetchone()
        assert status == "failed"
        assert json.loads(error_json)["error_type"]
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM event_cluster_memberships
            WHERE clustering_run_id = 'corrupt-selected'
            """
        ).fetchone()[0] == 0


def test_fingerprint_runs_without_write_lock_and_detects_source_change(
    tmp_path,
    monkeypatch,
):
    db = _create_db(tmp_path)
    _insert_news(
        db,
        "n1",
        "2026-08-20T10:00:00+00:00",
        "Acme publishes a sufficiently detailed document for concurrency test",
    )
    original = clustering_module.fingerprint_document
    mutated = False

    def fingerprint_with_concurrent_write(document, config):
        nonlocal mutated
        if not mutated:
            mutated = True
            with sqlite3.connect(db, timeout=0.5) as other:
                other.execute(
                    """
                    INSERT INTO news_documents(
                        news_id,
                        published_at,
                        ingested_at,
                        title
                    )
                    VALUES (
                        'concurrent-news',
                        '2026-08-21T10:00:00+00:00',
                        '2026-08-21T10:00:00+00:00',
                        'Concurrent source mutation'
                    )
                    """
                )
        return original(document, config)

    monkeypatch.setattr(
        clustering_module,
        "fingerprint_document",
        fingerprint_with_concurrent_write,
    )

    with pytest.raises(RuntimeError, match="base cambio"):
        run_clustering(
            db,
            source="news",
            max_documents=1,
            run_id="concurrent-change",
        )

    assert mutated is True
    with sqlite3.connect(db) as conn:
        status, error_json = conn.execute(
            """
            SELECT status, error_json
            FROM event_clustering_runs
            WHERE clustering_run_id = 'concurrent-change'
            """
        ).fetchone()
        assert status == "failed"
        assert "reproduciblemente" in json.loads(error_json)["message"]
        assert conn.execute(
            """
            SELECT COUNT(*)
            FROM event_cluster_memberships
            WHERE clustering_run_id = 'concurrent-change'
            """
        ).fetchone()[0] == 0

import json
import os
from pathlib import Path
import sqlite3
import subprocess

import pytest

from tools import project_context as ctx


def make_db(path):
    path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(path)
    conn.executescript("""
        CREATE TABLE facts(id INTEGER PRIMARY KEY, source_name TEXT, available_at TEXT,
                           retrieved_at TEXT, strict_pit INTEGER, feature_version TEXT, asset_id INTEGER);
        INSERT INTO facts VALUES(1,'SEC','2020-01-01','2026-08-28',0,'historical_v1',1);
        CREATE TABLE empty_table(id INTEGER);
        CREATE VIEW facts_view AS SELECT * FROM facts;
    """)
    conn.commit()
    conn.close()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / "repo"
    root.mkdir()
    for doc in ctx.CANONICAL:
        path = root / doc
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text("# Document\n", encoding="utf-8")
    (root / "docs/RESEARCH_STATUS.md").write_text("# Status\n1 states\n", encoding="utf-8")
    (root / ".gitignore").write_text("*.db\nreports/project_context/\n__pycache__/\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", str(root)], check=True)
    subprocess.run(["git", "-C", str(root), "add", "."], check=True)
    subprocess.run(["git", "-C", str(root), "-c", "user.name=Test", "-c", "user.email=test@example.invalid",
                    "commit", "-qm", "fixture"], check=True)
    make_db(root / "data/facts.db")
    return root


def config():
    return {
        "contract_version": ctx.VERSION,
        "databases": {
            "data/facts.db": {
                "role": "fixture",
                "profile_tables": ["facts"],
                "probes": [{"id": "facts", "sql": "SELECT COUNT(*) AS n FROM facts"}],
            }
        },
        "claims": [{
            "id": "fact_count", "document": "docs/RESEARCH_STATUS.md",
            "pattern": r"(\d+) states", "scope": "fixture", "database": "data/facts.db",
            "probe": "facts", "field": "n",
        }],
    }


def test_readonly_counts_sources_and_schema(repo):
    db = repo / "data/facts.db"
    before = ctx.sha_file(db)
    result = ctx.inspect_database(repo, "data/facts.db", config()["databases"]["data/facts.db"], 2, 10)
    assert result["status"] == "OBSERVED"
    assert ctx.scalar_result(result["tables"]["facts"]["count"]) == 1
    assert ctx.scalar_result(result["tables"]["empty_table"]["count"]) == 0
    assert result["tables"]["facts_view"]["count"]["status"] == "NOT_QUERIED"
    assert result["tables"]["facts"]["groups"]["source_name"]["rows"] == [{"value": "SEC", "n": 1}]
    assert result["tables"]["facts"]["coverage"]["rows"][0]["available_at__min"] == "2020-01-01"
    assert result["schema_sha256"]
    assert ctx.sha_file(db) == before


def test_missing_database_is_not_created_or_reported_zero(repo):
    result = ctx.inspect_database(repo, "missing.db", {}, 1, 1)
    assert result["status"] == "MISSING"
    assert not (repo / "missing.db").exists()
    assert not result["tables"]


def test_schema_only_has_no_fake_counts(repo):
    result = ctx.inspect_database(repo, "data/facts.db", config()["databases"]["data/facts.db"], 2, 10, True)
    assert ctx.scalar_result(result["tables"]["facts"]["count"]) is None
    assert result["probes"]["facts"]["status"] == "NOT_QUERIED"


@pytest.mark.parametrize("sql", [
    "INSERT INTO facts(id) VALUES(2)", "DELETE FROM facts", "VACUUM", "PRAGMA journal_mode=WAL",
    "ATTACH DATABASE 'unexpected.db' AS bad",
    "WITH x AS (SELECT 1) DELETE FROM facts",
    "SELECT load_extension('anything')",
])
def test_probe_cannot_write_or_load_extensions(repo, sql):
    path = repo / "data/facts.db"
    before = ctx.sha_file(path)
    reader = ctx.Reader(path, 1, 10)
    try:
        result = reader.query(sql)
        assert result["status"] == "ERROR"
        assert ctx.scalar_result(reader.query("SELECT COUNT(*) AS n FROM facts")) == 1
    finally:
        reader.conn.close()
    assert before == ctx.sha_file(path)
    assert not (repo / "unexpected.db").exists()


def test_budget_is_unknown_not_zero(repo):
    reader = ctx.Reader(repo / "data/facts.db", 1, 1)
    reader.deadline = -1
    try:
        result = reader.query("SELECT COUNT(*) AS n FROM facts")
        assert result["status"] == "SKIPPED_DATABASE_BUDGET"
        assert ctx.scalar_result(result) is None
    finally:
        reader.conn.close()


def test_query_timeout_does_not_poison_next_query(repo):
    reader = ctx.Reader(repo / "data/facts.db", 0.001, 10)
    try:
        result = reader.query("WITH RECURSIVE x(n) AS (SELECT 1 UNION ALL SELECT n+1 FROM x WHERE n<100000000) SELECT SUM(n) FROM x")
        assert result["status"] == "TIMEOUT"
        reader.query_seconds = 2
        assert ctx.scalar_result(reader.query("SELECT COUNT(*) AS n FROM facts")) == 1
    finally:
        reader.conn.close()


def test_quoted_table_name_and_uri_characters(tmp_path):
    db = tmp_path / "space # question?.db"
    conn = sqlite3.connect(db)
    conn.execute('CREATE TABLE "strange""name"(id INTEGER)')
    conn.close()
    result = ctx.inspect_database(tmp_path, db.name, {}, 1, 10)
    assert ctx.scalar_result(result["tables"]['strange"name']["count"]) == 0


def test_wal_is_read_not_ignored(repo):
    path = repo / "data/facts.db"
    writer = sqlite3.connect(path)
    writer.execute("PRAGMA journal_mode=WAL")
    writer.execute("INSERT INTO facts(id) VALUES(2)")
    writer.commit()
    try:
        result = ctx.inspect_database(repo, "data/facts.db", {}, 1, 10)
        assert ctx.scalar_result(result["tables"]["facts"]["count"]) == 2
        assert result["file_before"]["-wal"] is not None
    finally:
        writer.close()


def test_concurrent_write_invalidates_snapshot(repo, monkeypatch):
    original = ctx.Reader.query
    wrote = False
    def query(self, sql, params=(), limit=1000):
        nonlocal wrote
        result = original(self, sql, params, limit)
        if not wrote and "COUNT(*)" in sql:
            wrote = True
            with sqlite3.connect(repo / "data/facts.db") as writer:
                writer.execute("INSERT INTO facts(id) VALUES(2)")
        return result
    monkeypatch.setattr(ctx.Reader, "query", query)
    result = ctx.inspect_database(repo, "data/facts.db", {}, 1, 10)
    assert result["status"] == "CHANGED_DURING_READ_RETRY"


@pytest.mark.parametrize("relative", ["../outside.db", "/tmp/else.db"])
def test_paths_cannot_escape(repo, relative):
    with pytest.raises(ValueError):
        ctx.safe_path(repo, relative)


def test_symlink_and_private_data_not_followed(repo):
    outside = repo.parent / "external.db"
    make_db(outside)
    (repo / "alias.db").symlink_to(outside)
    (repo / ".env").write_text("PASSWORD=do-not-export", encoding="utf-8")
    (repo / "private.local.json").write_text('{"token":"do-not-export"}', encoding="utf-8")
    files, _ = ctx.inventory(repo)
    texts, _, _ = ctx.hash_and_references(repo, files)
    assert files["alias.db"]["kind"] == "SYMLINK_NOT_FOLLOWED"
    assert ".env" not in texts and "private.local.json" not in texts
    assert "sha256" not in files[".env"]
    assert ctx.inspect_database(repo, "alias.db", {}, 1, 1)["status"] == "UNAVAILABLE"


def test_reconciliation_match_mismatch_ambiguity_and_missing(repo):
    cfg = config()
    db = ctx.inspect_database(repo, "data/facts.db", cfg["databases"]["data/facts.db"], 2, 10)
    databases = {"data/facts.db": db}
    for content, expected in [("1 states", "MATCH"), ("2 states", "MISMATCH"),
                               ("1 states\n2 states", "DOCUMENT_MISSING_OR_AMBIGUOUS")]:
        result = ctx.reconcile(cfg, {"docs/RESEARCH_STATUS.md": content}, databases)
        assert result[0]["status"] == expected
    result = ctx.reconcile(cfg, {"docs/RESEARCH_STATUS.md": "1 states"}, {})
    assert result[0]["status"] == "UNKNOWN"
    assert result[0]["observed"] is None


def test_discovery_new_database_and_new_report(repo):
    make_db(repo / "new_layer.sqlite")
    reports = repo / "reports/new_run"
    reports.mkdir(parents=True)
    (reports / "summary.json").write_text('{"status":"COMPLETE","overall_interpretation":"FAIL"}', encoding="utf-8")
    result = ctx.build(repo, config(), 2, 10)
    assert result["databases"]["new_layer.sqlite"]["role"] == "UNCLASSIFIED_DATABASE_REVIEW"
    assert any(r["path"] == "reports/new_run/summary.json" for r in result["report_index"])
    assert result["scientific_promotion"] == "NOT_EVALUATED"


def test_git_inventory_offline_does_not_claim_remote(repo):
    files, _ = ctx.inventory(repo)
    result = ctx.git_inventory(repo, files)
    assert files["data/facts.db"]["git"] == "IGNORED"
    assert files["ARCHITECTURE.md"]["git"] == "TRACKED"
    assert result["live_remote"]["status"] == "NOT_CHECKED"


def test_referenced_ignored_sql_flagged(repo):
    (repo / ".gitignore").write_text("*.db\n*.sql\nreports/project_context/\n", encoding="utf-8")
    (repo / "database").mkdir()
    (repo / "database/current.sql").write_text("CREATE TABLE sample(id);", encoding="utf-8")
    (repo / "tools").mkdir()
    (repo / "tools/run.py").write_text('SCHEMA = "database/current.sql"\n', encoding="utf-8")
    result = ctx.build(repo, config(), 1, 10)
    assert any(f["code"] == "IGNORED_CODE_DEPENDENCY" for f in result["findings"])


def test_cleanup_protects_failed_research_and_identical_files(repo):
    reports = repo / "reports/failure"
    reports.mkdir(parents=True)
    for name in ("summary.json", "copy.json"):
        (reports / name).write_text('{"status":"FAIL"}\n', encoding="utf-8")
    (repo / "__pycache__").mkdir()
    (repo / "__pycache__/cache.pyc").write_bytes(b"cache")
    result = ctx.build(repo, config(), 2, 10)
    cleanup = result["cleanup"]
    assert cleanup["mode"] == "DRY_RUN_ONLY"
    assert any(len(g["paths"]) == 2 for g in cleanup["identical_small_files"])
    assert all(c["automatic_deletion_allowed"] is False for c in cleanup["candidates"])
    assert not any(c["path"].startswith("reports/") for c in cleanup["candidates"])
    assert not any(c["path"].endswith(".db") for c in cleanup["candidates"])
    assert (reports / "summary.json").exists()


def test_freshness_detects_change_without_db_queries(repo, monkeypatch):
    result = ctx.build(repo, config(), 2, 10)
    ctx.write_outputs(repo, result)
    monkeypatch.setattr(ctx, "inspect_database", lambda *a, **k: pytest.fail("must not query DB"))
    assert ctx.check_freshness(repo)["status"] == "FRESH_WITHIN_METADATA_SCOPE"
    (repo / "docs/ROADMAP.md").write_text("# Changed\n", encoding="utf-8")
    assert ctx.check_freshness(repo)["status"] == "STALE"


def test_output_refuses_unowned_or_symlink_target(repo):
    path = repo / ctx.OUTPUT
    path.mkdir(parents=True)
    (path / "CONTEXT.md").write_text("user content", encoding="utf-8")
    with pytest.raises(ValueError):
        ctx.write_outputs(repo, {})
    assert (path / "CONTEXT.md").read_text() == "user content"


def test_missing_output_check_and_cli_budgets(repo):
    assert ctx.check_freshness(repo)["status"] == "MISSING"
    with pytest.raises(SystemExit):
        ctx.main(["--root", str(repo), "--query-seconds", "0"])


def test_frozen_artifact_hash_without_deserialization(repo):
    path = repo / "model.joblib"
    path.write_bytes(b"not even a serialized model")
    row = {"fit_id": "fit", "artifact_path": "model.joblib", "artifact_sha256": ctx.sha_file(path)}
    databases = {"db": {"probes": {"frozen_fits": {"status": "EXACT", "rows": [row]}}}}
    assert ctx.artifact_checks(repo, databases)[0]["status"] == "MATCH"
    path.write_bytes(b"changed")
    assert ctx.artifact_checks(repo, databases)[0]["status"] == "MISMATCH"


def test_report_is_not_promoted_and_payload_is_not_exported():
    value = {"status": "PASS", "raw_payload_json": {"password": "private"},
             "horizon_matrix": {"1": {"gate": {"status": "FAIL"}}}}
    signals = ctx.report_signals(value)
    assert signals["/status"] == "PASS"
    assert signals["/horizon_matrix/1/gate/status"] == "FAIL"
    assert "private" not in json.dumps(signals)


def test_redaction():
    text = ctx.redact("mail=a@company.com https://user:password@host.test/page?api_key=secret token=abc")
    assert "a@company" not in text
    assert "user:password" not in text
    assert "api_key=secret" not in text
    assert "token=abc" not in text


def test_repository_config_matches_readonly_contract():
    root = Path(__file__).resolve().parents[1]
    cfg = json.loads((root / "config/project_context_v001.json").read_text())
    assert cfg["contract_version"] == ctx.VERSION
    for path, spec in cfg["databases"].items():
        assert not Path(path).is_absolute()
        for probe in spec.get("probes", []):
            assert probe["sql"].startswith("SELECT ")


@pytest.mark.parametrize("value,state", [(0.0047032, "MATCH"), (0.01, "MISMATCH"), (None, "REPORT_SCHEMA_DRIFT")])
def test_document_number_vs_report_is_not_reproduction(value, state):
    cfg = {"report_checks": [{"id": "delta", "document": "docs/status.md", "pattern": r"delta: ([\d.]+)",
                             "report": "reports/result.json", "pointer": "/gate/delta", "tolerance": 0.0000005}]}
    texts = {"docs/status.md": "delta: 0.004703", "reports/result.json": json.dumps({"gate": {"delta": value}})}
    row = ctx.reconcile_reports(cfg, texts)[0]
    assert row["status"] == state
    assert row["kind"] == "DOCUMENT_VS_SAVED_REPORT_NOT_REPRODUCED"


def test_output_companion_tampering_marks_stale(repo):
    ctx.write_outputs(repo, ctx.build(repo, config(), 2, 10))
    (repo / ctx.OUTPUT / "CONTEXT.md").write_text("wrong context", encoding="utf-8")
    result = ctx.check_freshness(repo)
    assert result["status"] == "STALE"
    assert "output_incomplete_or_modified" in result["reasons"]


def test_concurrent_output_writer_cannot_overwrite(repo):
    result = ctx.build(repo, config(), 2, 10)
    ctx.write_outputs(repo, result)
    lock = repo / ctx.OUTPUT / ".write-lock"
    lock.touch()
    before = (repo / ctx.OUTPUT / "context.json").read_bytes()
    with pytest.raises(FileExistsError):
        ctx.write_outputs(repo, result)
    assert before == (repo / ctx.OUTPUT / "context.json").read_bytes()
    assert ctx.check_freshness(repo)["status"] == "STALE"


@pytest.mark.parametrize("value", ["nan", "inf", "-1"])
def test_nonfinite_or_negative_budget_rejected(repo, value):
    with pytest.raises(SystemExit):
        ctx.main(["--root", str(repo), "--query-seconds", value])


def test_required_sql_is_not_hidden_by_repository_gitignore():
    root = Path(__file__).resolve().parents[1]
    for path in ["database/information_capture_v001_schema.sql",
                 "database/information_capture_v0013_additive.sql",
                 "database/news_narrative_evidence_v001_additive.sql",
                 "database/reference/global_market_reference_v0052.sql"]:
        rc, output, error = ctx.run_git(root, "check-ignore", "--no-index", path)
        assert rc == 1, (path, output, error)


def test_new_database_gets_metadata_profile_without_mapping(repo):
    result = ctx.inspect_database(repo, "data/facts.db", {}, 2, 10)
    assert result["tables"]["facts"]["groups"]["source_name"]["rows"] == [{"value": "SEC", "n": 1}]
    assert result["role"] == "UNCLASSIFIED_DATABASE_REVIEW"


def test_fixture_and_context_output_references_are_not_missing_data(repo):
    (repo / "tests").mkdir()
    (repo / "tests/example.py").write_text('PATH = "reports/fixture/result.json"\n', encoding="utf-8")
    (repo / "docs/entry.md").write_text("reports/project_context/latest/CONTEXT.md", encoding="utf-8")
    result = ctx.build(repo, config(), 2, 10)
    assert "reports/fixture/result.json" in result["references"]
    missing = [f["details"]["path"] for f in result["findings"]
               if f["code"] == "REFERENCE_NOT_FOUND_REVIEW_NOT_PROOF_OF_ERROR"]
    assert "reports/fixture/result.json" not in missing
    assert "reports/project_context/latest/CONTEXT.md" not in missing

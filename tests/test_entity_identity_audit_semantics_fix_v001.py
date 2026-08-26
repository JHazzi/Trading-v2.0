import sqlite3
from pathlib import Path
import json

from evaluation.events.entity_identity_audit_v001 import audit


def test_fixed_audit_field_names(tmp_path):
    db = tmp_path / "identity.db"
    with sqlite3.connect(db) as c:
        c.executescript("""
        CREATE TABLE identity_runs(
          status TEXT,
          profiles_written INTEGER,
          candidate_pairs_written INTEGER
        );
        INSERT INTO identity_runs VALUES ('completed',1,1);

        CREATE TABLE identity_name_profiles(
          registry_name_id TEXT PRIMARY KEY
        );
        INSERT INTO identity_name_profiles VALUES ('n1');

        CREATE TABLE identity_candidate_pairs(
          candidate_kind TEXT,
          auto_merge_allowed INTEGER,
          same_accession_cooccurrence INTEGER
        );
        INSERT INTO identity_candidate_pairs VALUES
          ('punctuation_spacing_variant',0,0);
        """)
        c.commit()

    cfg = tmp_path / "cfg.json"
    cfg.write_text(json.dumps({"output_db": str(db)}))

    # audit ROOT-joins absolute paths safely via pathlib semantics.
    out = audit(cfg)
    contract = out["identity_contract"]
    assert contract["automatic_merge_allowed_by_contract"] is False
    assert contract["automatic_merge_candidate_rows"] == 0
    assert contract["automatic_merge_performed"] is False
    assert "automatic_merge_allowed" not in contract

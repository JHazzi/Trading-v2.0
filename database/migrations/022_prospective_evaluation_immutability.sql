PRAGMA foreign_keys = ON;

-- Prospective Registry V001 hardening.
-- Migration 021 is already applied and must remain unchanged. This additive
-- migration makes evaluation and audit records append-only at the database
-- boundary, matching the preregistered V009 contract.

CREATE TRIGGER IF NOT EXISTS trg_prospective_evaluations_no_update
BEFORE UPDATE ON prospective_evaluation_runs
BEGIN SELECT RAISE(ABORT, 'prospective evaluations are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_prospective_evaluations_no_delete
BEFORE DELETE ON prospective_evaluation_runs
BEGIN SELECT RAISE(ABORT, 'prospective evaluations are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_prospective_audits_no_update
BEFORE UPDATE ON prospective_registry_audits
BEGIN SELECT RAISE(ABORT, 'prospective audits are immutable'); END;

CREATE TRIGGER IF NOT EXISTS trg_prospective_audits_no_delete
BEFORE DELETE ON prospective_registry_audits
BEGIN SELECT RAISE(ABORT, 'prospective audits are immutable'); END;

INSERT OR IGNORE INTO schema_migrations(version, name)
VALUES ('022', 'prospective_evaluation_immutability');

"""SEC close-aligned dataset V002: provenance-aware clocks, never a model fit.

V001's mechanics are reused through explicit policy hooks, not global mutation.
HTTP modification metadata is retained but cannot manufacture event arrivals.
"""
from __future__ import annotations

import argparse
from collections import Counter, defaultdict
from contextlib import closing
from datetime import timedelta
from email.utils import parsedate_to_datetime
import json
import math
from pathlib import Path
import re
import sqlite3

from . import distributional_dataset_v001 as engine

ROOT = engine.ROOT
CONTRACT = "distributional_event_close_aligned_v002"
CLOCK_POLICY = "sec_acceptance_proxy_http_metadata_separate_v002"
DEFAULT_CONFIG = ROOT / "config/distributional_event_dataset_v002.json"
OUTPUT_ROOT = "reports/distributional_event_dataset_v002"
BOUNDARY_BASIS = "state_time_with_validated_asof_sec_acceptance_proxies"
# This adapter is tied to the persisted SEC downloader contract, not an
# assumption that every source's generic modified_at field is HTTP metadata.
EVIDENCE_SQL = engine.EVIDENCE_SQL.replace(
    "r.available_at raw_available_at,r.modified_at,r.raw_sha256,",
    "r.available_at raw_available_at,r.modified_at,r.raw_sha256,"
    "r.metadata_json raw_metadata_json,fv.observed_at version_observed_at,")
AVAILABILITY_FIELDS = (
    "evidence_available_at", "published_at", "raw_available_at",
    "acceptance_datetime", "link_available_at", "semantic_available_at",
)


def validate_contract(cfg: dict) -> dict:
    expected = dict(dataset_contract=CONTRACT, clock_policy=CLOCK_POLICY,
                    label_version="event_distributional_close_aligned_v002",
                    projection_version="event_arrival_set_v002", output_root=OUTPUT_ROOT,
                    http_modified_policy="verified_http_metadata_never_information_availability",
                    maximum_unexplained_boundary_shift_seconds=0,
                    corporate_action_review_fraction=0.10)
    if any(cfg.get(k) != v for k, v in expected.items()):
        raise ValueError("unsupported_v002_dataset_contract")
    # Preserve source/window/market/version constraints from the shared engine.
    engine.validate_contract(dict(cfg, dataset_contract=engine.CONTRACT,
        label_version="event_distributional_close_aligned_v001",
        projection_version="event_arrival_set_v001", clock_policy="historical_public_proxy"))
    return cfg


def load_contract(path: Path) -> dict:
    return validate_contract(json.loads(path.read_text(encoding="utf-8")))


def verified_sec_clock(row: dict, state_time) -> str:
    """Validate provenance; do not infer old byte identity from an HTTP header."""
    if row["source_id"] != "sec_edgar":
        raise ValueError("unverified_sec_clock_source")
    metadata = json.loads(row["raw_metadata_json"])
    if not isinstance(metadata, dict) or metadata.get("exact_response_entity_bytes") is not True:
        raise ValueError("unverified_raw_response_provenance")
    if metadata.get("availability_source") != "sec_acceptance_datetime":
        raise ValueError("revision_or_unknown_raw_availability_provenance")
    accepted = engine.utc(row["acceptance_datetime"])
    if any(engine.utc(row[k]) != accepted for k in
           ("evidence_available_at", "published_at", "raw_available_at")):
        raise ValueError("sec_acceptance_proxy_clock_mismatch")
    if any(row.get(k) and engine.utc(row[k]) > state_time for k in AVAILABILITY_FIELDS):
        raise ValueError("information_clock_after_snapshot_requires_review")
    if engine.utc(row["version_observed_at"]) < engine.utc(row["retrieved_at"]):
        raise ValueError("version_observation_precedes_raw_retrieval")
    headers = metadata.get("response_headers")
    if not isinstance(headers, dict):
        raise ValueError("missing_http_header_provenance")
    modified_headers = [v for k, v in headers.items() if k.lower() == "last-modified"]
    if len(modified_headers) > 1:
        raise ValueError("ambiguous_http_modified_provenance")
    header = modified_headers[0] if modified_headers else None
    if header is None and row["modified_at"] is None:
        return "absent"
    if not isinstance(header, str) or not header or not row["modified_at"]:
        raise ValueError("unverified_http_modified_metadata")
    parsed = parsedate_to_datetime(header)
    if parsed is None or parsed.tzinfo is None or (
        engine.utc(parsed.isoformat()) != engine.utc(row["modified_at"])
    ):
        raise ValueError("http_modified_header_mismatch")
    return "http_last_modified_metadata_only"


def clock_diagnostic(state: dict, members: list[dict]) -> dict:
    t = engine.utc(state["state_time"])
    modified = [engine.utc(r["modified_at"]) for r in members if r["modified_at"]]
    latest = max(modified) if modified else None
    # Counterfactual is a diagnostic of the rejected V001 rule, NEVER a gate.
    legacy_boundary = max([t, *modified])
    return dict(http_modified_members=len(modified),
                http_modified_after_snapshot_members=sum(m > t for m in modified),
                latest_http_modified_at=engine.iso(latest) if latest else None,
                counterfactual_v001_boundary=engine.iso(legacy_boundary),
                counterfactual_v001_shift_seconds=(legacy_boundary - t).total_seconds())


def prepare_state(state: dict, evidence: list[dict], cfg: dict) -> dict:
    t = engine.utc(state["state_time"])
    if state["feature_version"] != cfg["event_feature_version"]:
        raise ValueError("event_feature_version_mismatch")
    if engine.utc(state["available_at"]) > t:
        raise ValueError("state_availability_after_snapshot_requires_review")
    originals = {}
    for row in evidence:
        if engine.utc(row["evidence_available_at"]) > t:
            continue
        key = row["membership_id"]
        if key in originals and originals[key] != row:
            raise ValueError("ambiguous_membership_lineage")
        originals[key] = row
    # Reuse identity/count/taxonomy/dedup/revision checks, removing only the
    # unsupported clock from the legacy algebra. Originals are restored below.
    prepared = engine.prepare_state(
        state, [dict(r, modified_at=None) for r in evidence],
        dict(cfg, clock_policy="historical_public_proxy"))
    bases = {key: verified_sec_clock(row, t) for key, row in originals.items()}
    if engine.utc(prepared["boundary"]) != t:
        raise ValueError("unexplained_boundary_shift_requires_review")
    prepared["memberships"] = [originals[r["membership_id"]] for r in prepared["memberships"]]
    prepared["evidence"] = [
        dict(r, modified_at=originals[r["membership_id"]]["modified_at"],
             modified_at_basis=bases[r["membership_id"]],
             availability_basis_verified="sec_acceptance_historical_proxy_not_observed_bytes")
        for r in prepared["evidence"]]
    prepared.update(clock_policy=CLOCK_POLICY, boundary_basis=BOUNDARY_BASIS,
                    historical_bytes_verified=False,
                    clock_diagnostic=clock_diagnostic(state, prepared["memberships"]))
    return prepared


def lineage_diagnostic(row: dict, payload: dict) -> dict:
    """As-of foreign memberships are quarantine evidence, never model features."""
    state = payload["state"]
    t = engine.utc(state["state_time"])
    match = re.fullmatch(r"sec:(\d{10}-\d{2}-\d{6}):.+", state["identity_key"])
    own = match.group(1) if match else None
    candidates = payload.get("candidate_memberships_for_review_only", [])
    asof = [r for r in candidates if engine.utc(r["evidence_available_at"]) <= t]
    foreign = [r for r in asof if r.get("accession_number") != own]
    own_rows = [r for r in asof if r.get("accession_number") == own]
    by_hash = defaultdict(set)
    for r in asof:
        if r.get("raw_sha256") and r.get("accession_number"):
            by_hash[r["raw_sha256"]].add(r["accession_number"])
    return dict(event_state_id=row["event_state_id"], event_id=row["event_id"],
                ticker=state["ticker"], event_type=state["event_type"],
                state_time=state["state_time"], reason=row["reason"], own_accession=own,
                foreign_accessions=sorted({r["accession_number"] for r in foreign if r.get("accession_number")}),
                own_memberships=len({r["membership_id"] for r in own_rows}),
                foreign_memberships=len({r["membership_id"] for r in foreign}),
                asof_match_methods=dict(Counter(r["match_method"] for r in asof)),
                cross_accession_shared_content_hashes=sum(len(a) > 1 for a in by_hash.values()),
                future_members_not_used=len(candidates) - len(asof),
                disposition="QUARANTINE_NO_AUTOMATIC_MERGE_OR_STATE_REWRITE")


def audit_temporal_and_lineage(conn: sqlite3.Connection, manifest: dict) -> dict:
    """Independent invariants in addition to replaying the preparation code."""
    failures, review = [], []
    cfg = manifest["contract"]
    try:
        validate_contract(cfg)
    except (ValueError, KeyError, TypeError):
        failures.append("UNSUPPORTED_PERSISTED_V002_CONTRACT")
    states = [dict(r) for r in conn.execute("SELECT * FROM state_audit")]
    samples = [dict(r) for r in conn.execute("SELECT * FROM samples")]
    payloads = {r["event_state_id"]: json.loads(r["payload_json"]) for r in states}
    eligible = {r["event_state_id"] for r in states if r["status"] == "eligible"}
    alignments = [dict(r) for r in conn.execute("SELECT * FROM alignment_audit")]
    links = [dict(r) for r in conn.execute("""SELECT se.*,s.delay_seconds,s.origin_day
        FROM sample_events se JOIN samples s USING(sample_id)""")]
    alignment_map = {(r["event_state_id"], r["delay_seconds"]): r for r in alignments}
    expected_alignment = {(sid, delay) for sid in eligible for delay in cfg["delay_sensitivity_seconds"]}
    if set(alignment_map) != expected_alignment:
        failures.append("INCOMPLETE_OR_FOREIGN_STATE_ALIGNMENT")
    selected_links = Counter((r["event_state_id"], r["delay_seconds"]) for r in links)
    for key, row in alignment_map.items():
        if selected_links[key] != int(row["status"] == "selected"):
            failures.append("SELECTED_ALIGNMENT_SAMPLE_LINK_COUNT_MISMATCH")
        if row["status"] not in {"selected", "superseded_within_session", "excluded"}:
            failures.append("UNKNOWN_ALIGNMENT_STATUS")
    sample_links = defaultdict(list)
    for row in links:
        sample_links[row["sample_id"]].append(row)
        p = payloads[row["event_state_id"]]
        if row["event_state_id"] not in eligible or row["event_id"] != p["state"]["event_id"] or (
            row["accession"] != p.get("accession") or row["boundary"] != p.get("boundary")
        ):
            failures.append("SAMPLE_EVENT_IDENTITY_OR_BOUNDARY_MISMATCH")
    for sample in samples:
        ps = [payloads[r["event_state_id"]] for r in sample_links[sample["sample_id"]]]
        if ps:
            cutoff = max(engine.utc(p["boundary"]) + timedelta(seconds=sample["delay_seconds"]) for p in ps)
            if engine.utc(sample["information_cutoff"]) != cutoff:
                failures.append("INFORMATION_CUTOFF_PROVENANCE_MISMATCH")

    shifts, corrected, lineage, year_counts = [], [], [], defaultdict(Counter)
    for row in states:
        p = payloads[row["event_state_id"]]
        state = p["state"]
        year = engine.utc(state["state_time"]).year
        year_counts[year]["source_states"] += 1
        if row["status"] != "eligible":
            year_counts[year]["excluded_states"] += 1
            if row["reason"] in {"cross_accession_evidence_requires_review",
                                 "text_cluster_requires_separate_causal_review",
                                 "ambiguous_membership_lineage"}:
                try:
                    lineage.append(lineage_diagnostic(row, p))
                except (ValueError, KeyError, TypeError):
                    failures.append("UNREADABLE_QUARANTINE_PROVENANCE")
            continue
        year_counts[year]["eligible_states"] += 1
        a = alignment_map.get((row["event_state_id"], 0))
        if a:
            year_counts[year]["base_" + a["status"] + "_states"] += 1
        try:
            t = engine.utc(state["state_time"])
            boundary = engine.utc(p["boundary"])
            # This invariant is NOT obtained from prepare_state. Every admitted
            # SEC acceptance proxy belongs to this snapshot; nothing may shift
            # its boundary past state_time, irrespective of an HTTP timestamp.
            allowed = [t, engine.utc(state["available_at"]),
                       engine.utc(state["observation_available_at"])]
            for member in p["memberships"]:
                allowed.extend(engine.utc(member[k]) for k in AVAILABILITY_FIELDS if member.get(k))
            shift = (boundary - t).total_seconds()
            shifts.append(shift)
            if boundary != max(allowed) or boundary != t or p["boundary_shift_seconds"] != shift:
                failures.append("UNEXPLAINED_INFORMATION_BOUNDARY_SHIFT")
            if p.get("clock_policy") != CLOCK_POLICY or p.get("boundary_basis") != BOUNDARY_BASIS or (
                p.get("historical_bytes_verified") is not False
            ):
                failures.append("CLOCK_PROVENANCE_POLICY_MISMATCH")
            diagnostic = clock_diagnostic(state, p["memberships"])
            if p.get("clock_diagnostic") != diagnostic:
                failures.append("HTTP_CLOCK_DIAGNOSTIC_MISMATCH")
            if diagnostic["counterfactual_v001_shift_seconds"] > 0:
                corrected.append(dict(event_state_id=row["event_state_id"], ticker=state["ticker"],
                    state_time=state["state_time"], boundary=p["boundary"], **diagnostic))
            if any(r["modified_at"] and engine.utc(r["modified_at"]) > engine.utc(r["retrieved_at"])
                   for r in p["memberships"]):
                review.append("HTTP_MODIFIED_METADATA_AFTER_ACTUAL_RETRIEVAL")
        except (ValueError, KeyError, TypeError):
            failures.append("UNREADABLE_TEMPORAL_PROVENANCE")
    if lineage:
        review.append("AMBIGUOUS_FILING_LINKS_QUARANTINED")
    if any(r["status"] == "excluded" for r in alignments):
        review.append("ALIGNMENT_EXCLUSIONS_REQUIRE_REVIEW")
    for year, counts in year_counts.items():
        if counts["eligible_states"] and not counts["base_selected_states"]:
            review.append(f"SOURCE_YEAR_WITHOUT_SELECTED_BASE_STATES_{year}")
    scenario_coverage, corporate = [], []
    for delay in cfg["delay_sensitivity_seconds"]:
        ss = [s for s in samples if s["delay_seconds"] == delay]
        sl = [r for r in links if r["delay_seconds"] == delay]
        days = {s["origin_day"] for s in ss}
        scenario_coverage.append(dict(delay_seconds=delay, samples=len(ss),
            origin_days=len(days), assets=len({s["asset_id"] for s in ss}),
            selected_states=len(sl), unique_events=len({r["event_id"] for r in sl}),
            unique_filings=len({r["accession"] for r in sl}),
            first_origin=min(days) if days else None, last_origin=max(days) if days else None))
        for h in cfg["horizons_sessions"]:
            n = conn.execute("""SELECT COUNT(*) FROM outcomes o JOIN samples s USING(sample_id)
                WHERE s.delay_seconds=? AND o.horizon_sessions=? AND o.reason='corporate_action_overlap'""",
                (delay, h)).fetchone()[0]
            fraction = n / len(ss) if ss else None
            corporate.append(dict(delay_seconds=delay, horizon_sessions=h, excluded=n,
                                  samples=len(ss), fraction=fraction))
            if fraction is not None and fraction >= cfg["corporate_action_review_fraction"]:
                review.append(f"CORPORATE_ACTION_SELECTION_SCENARIO_{delay}_H{h}")
    concentration = Counter((r["origin_day"], payloads[r["event_state_id"]]["state"]["ticker"])
                            for r in links if r["delay_seconds"] == 0)
    legacy_shifts = [r["counterfactual_v001_shift_seconds"] for r in corrected]
    return dict(failures=failures, review=review, scenario_coverage=scenario_coverage,
        temporal_audit=dict(clock_policy=CLOCK_POLICY,
            eligible_states=len(eligible), unexplained_shift_states=sum(s != 0 for s in shifts),
            maximum_boundary_shift_seconds=max(shifts, default=0),
            states_shifted_by_rejected_v001_rule=len(corrected),
            rejected_v001_shifts_over_one_day=sum(s > 86400 for s in legacy_shifts),
            rejected_v001_shifts_over_30_days=sum(s > 30 * 86400 for s in legacy_shifts),
            maximum_rejected_v001_shift_seconds=max(legacy_shifts, default=0),
            worst_rejected_v001_shifts=sorted(corrected,
                key=lambda r: (-r["counterfactual_v001_shift_seconds"], r["event_state_id"]))[:20]),
        state_year_coverage=[dict(state_year=y, **dict(c)) for y, c in sorted(year_counts.items())],
        base_arrival_concentration=[dict(origin_day=day, ticker=ticker, selected_events=n)
            for (day, ticker), n in concentration.most_common(20)],
        corporate_action_selection=corporate,
        lineage_review=dict(states=len(lineage), events=len({r["event_id"] for r in lineage}),
            by_asset=dict(Counter(r["ticker"] for r in lineage)),
            by_reason=dict(Counter(r["reason"] for r in lineage)),
            by_event_type=dict(Counter(r["event_type"] for r in lineage)),
            details=lineage))


def render_report(report: dict) -> str:
    temporal = report["temporal_audit"]
    lines = [engine.render_report(report), "## Control temporal V002", "",
        "Last-Modified se conserva como metadato HTTP verificado, no como llegada de información.",
        f"Estados elegibles: {temporal['eligible_states']}.",
        f"Desplazamientos temporales injustificados: {temporal['unexplained_shift_states']}.",
        f"Máximo desplazamiento real de frontera: {temporal['maximum_boundary_shift_seconds']} segundos.",
        f"Estados que V001 habría desplazado más de un día: {temporal['rejected_v001_shifts_over_one_day']}.",
        f"Mayor desplazamiento de la regla rechazada: {temporal['maximum_rejected_v001_shift_seconds'] / 86400:.3f} días.",
        "", "## Cobertura por escenario", "",
        "| Retraso (s) | Muestras | Días | Activos | Estados | Filings | Primer origen | Último origen |",
        "|---:|---:|---:|---:|---:|---:|---|---|"]
    for r in report["scenario_coverage"]:
        lines.append(f"| {r['delay_seconds']} | {r['samples']} | {r['origin_days']} | {r['assets']} | "
                     f"{r['selected_states']} | {r['unique_filings']} | {r['first_origin']} | {r['last_origin']} |")
    lines.extend(["", "## Estados excluidos", ""])
    lines.extend(f"- {reason}: {n}" for reason, n in report["state_exclusions"].items())
    lineage = report["lineage_review"]
    lines.extend(["", "## Vínculos entre filings en cuarentena", "",
        f"Estados: {lineage['states']}; eventos distintos: {lineage['events']}.",
        "No se fusionaron identidades, no se borró evidencia y no se reescribieron estados de origen.",
        "audit.json detalla accessions propios/ajenos, métodos y membresías futuras ignoradas.",
        "La concentración por fecha y la pérdida por año/acciones corporativas también quedan explícitas.",
        "", "V001 se conserva como evidencia de un contrato temporal rechazado, no como datos entrenables.",
        "La corrección no demuestra primera divulgación pública ni identidad histórica de los bytes.",
        "Revisar la ejecución completa y preregistrar el experimento antes de entrenar.", ""])
    return "\n".join(lines)


def policy() -> engine.DatasetPolicy:
    return engine.DatasetPolicy(CONTRACT, prepare_state, EVIDENCE_SQL,
        extra_audit=audit_temporal_and_lineage, render=render_report,
        code_paths=(Path(__file__), ROOT / "ingestion/events/sec_filing_documents.py"))


def build(cfg: dict, output: Path, source_path: Path, market_path: Path,
          market_features: list[str], max_states: int | None = None,
          query_seconds: float = 30) -> dict:
    validate_contract(cfg)
    return engine.build(cfg, output, source_path, market_path, market_features,
                        max_states, query_seconds, policy=policy())


def audit(db: Path) -> dict:
    return engine.audit(db, policy=policy())


def audit_artifact(output: Path, cfg: dict) -> dict:
    """Read-only replay; reject a mismatched sidecar/config before interpretation."""
    manifest = json.loads((output / "manifest.json").read_text(encoding="utf-8"))
    db = output / "dataset.sqlite"
    if manifest["contract"] != cfg:
        raise ValueError("audit_config_does_not_match_persisted_contract")
    if engine.file_digest(db) != manifest["dataset_sha256"]:
        raise ValueError("output_database_hash_mismatch")
    with closing(engine.ro_connect(db)) as conn:
        persisted = json.loads(conn.execute("SELECT value_json FROM metadata WHERE key='manifest'").fetchone()[0])
    if {k: v for k, v in manifest.items() if k != "dataset_sha256"} != persisted:
        raise ValueError("manifest_sidecar_database_mismatch")
    return audit(db)


def main() -> None:
    ap = argparse.ArgumentParser(description=__doc__)
    ap.add_argument("--stage", choices=("build", "audit"), required=True)
    ap.add_argument("--config", type=Path, default=DEFAULT_CONFIG)
    ap.add_argument("--run-id", required=True)
    ap.add_argument("--max-states", type=int)
    ap.add_argument("--query-seconds", type=float, default=30)
    args = ap.parse_args()
    if not re.fullmatch(r"[A-Za-z0-9][A-Za-z0-9_-]{0,79}", args.run_id):
        ap.error("run-id must be a simple name, not a path")
    if args.max_states is not None and args.max_states <= 0:
        ap.error("max-states must be positive")
    if not math.isfinite(args.query_seconds) or args.query_seconds <= 0:
        ap.error("query-seconds must be finite and positive")
    cfg = load_contract(args.config)
    output_root = ROOT / OUTPUT_ROOT
    allowed = ROOT.resolve() / "reports" / "distributional_event_dataset_v002"
    output = output_root / args.run_id
    if output_root.resolve() != allowed or output.is_symlink() or not output.resolve().is_relative_to(allowed):
        ap.error("unsafe output path")
    if args.stage == "build":
        from features.market.daily_v003_core import OWN_FEATURES
        spec = json.loads((ROOT / cfg["market_specification"]).read_text())
        if sorted(spec["frozen_own_features"]) != sorted(OWN_FEATURES):
            raise ValueError("market_feature_specification_changed")
        report = build(cfg, output, ROOT / cfg["source_database"], ROOT / cfg["market_database"],
                       spec["frozen_own_features"], args.max_states, args.query_seconds)
    else:
        if args.max_states is not None:
            ap.error("max-states applies only to build, never audit")
        report = audit_artifact(output, cfg)
    print(json.dumps({k: report[k] for k in (
        "status", "integrity_status", "dataset_contract", "examined_states", "source_states",
        "samples", "failures", "review", "training_authorized", "scenario_coverage")}, indent=2))
    raise SystemExit(1 if report["integrity_status"] == "FAIL" else 2)


if __name__ == "__main__":
    main()

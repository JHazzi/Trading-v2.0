from __future__ import annotations

import argparse
import hashlib
import json
import os
import sqlite3
import uuid
from datetime import datetime, timezone
from pathlib import Path

from ingestion.events.sec_metadata_logic import (
    classify_observation,
    canonical_metadata_version_reference,
)

from ingestion.events.sec_edgar_v2 import (
    SOURCE_ID, SecClient, RawStore, archive_document_url,
    collect_submission_payloads, filing_payload, iter_columnar_filings,
    normalize_cik, normalize_timestamp, parse_forms,
    persist_submission_response, ticker_mapping, validate_user_agent,
)

INGESTION_VERSION = "sec_metadata_v0.3.0"
DEFAULT_DB = Path("data/database/market_data_v2.db")
DEFAULT_RAW_ROOT = Path("data/raw")
DEFAULT_FORMS = ("8-K","8-K/A","10-Q","10-Q/A","10-K","10-K/A","6-K","6-K/A")


def utc_now() -> str:
    return datetime.now(timezone.utc).isoformat()


def stable_id(kind: str, *parts: object) -> str:
    material = "\0".join((kind, *(str(x) for x in parts))).encode("utf-8")
    return hashlib.sha256(material).hexdigest()


def canonical_json(value: object) -> str:
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(",", ":"))



def ensure_contract(conn: sqlite3.Connection) -> None:
    required = {
        "source_ingestion_runs", "raw_source_documents", "raw_document_assets",
        "sec_filings", "sec_submission_retrievals",
        "sec_filing_metadata_versions", "sec_filing_metadata_observations",
    }
    existing = {str(r[0]) for r in conn.execute(
        "SELECT name FROM sqlite_master WHERE type='table'"
    )}
    missing = sorted(required - existing)
    if missing:
        raise RuntimeError(f"SEC v3 requiere 011+016; faltan {missing}")


def persist_submission_retrieval(conn, store, *, run_id, cik, source_url,
                                 external_id, storage_name, payload, retrieved_at):
    raw_id = persist_submission_response(
        conn, store, cik=cik, source_url=source_url, external_id=external_id,
        storage_name=storage_name, payload=payload, retrieved_at=retrieved_at,
    )
    request_identity = f"{run_id}:{external_id}"
    retrieval_id = stable_id("sec_submission_retrieval", raw_id, request_identity)
    conn.execute(
        """INSERT OR IGNORE INTO sec_submission_retrievals(
             submission_retrieval_id,raw_document_id,ingestion_run_id,external_id,
             source_url,request_identity,observed_at,retrieved_at,provenance_status,metadata_json
           ) VALUES(?,?,?,?,?,?,?,?, 'native', ?)""",
        (retrieval_id, raw_id, run_id, external_id, source_url, request_identity,
         retrieved_at, retrieved_at,
         canonical_json({"exact_fetch_observation": True, "version": INGESTION_VERSION}))
    )
    return raw_id, retrieval_id


def _asset_id(conn, ticker):
    if not ticker:
        return None
    row = conn.execute(
        "SELECT asset_id FROM assets WHERE UPPER(ticker)=UPPER(?) ORDER BY active DESC, asset_id LIMIT 1",
        (ticker,),
    ).fetchone()
    return None if row is None else int(row[0])


def _link_asset(conn, raw_id, ticker):
    aid = _asset_id(conn, ticker)
    if aid is None:
        return
    conn.execute(
        """INSERT OR IGNORE INTO raw_document_assets(
             raw_document_id,asset_id,role,linking_method,linking_version,confidence,metadata_json
           ) VALUES(?,?,'issuer','sec_ticker_cik',?,1.0,?)""",
        (raw_id, aid, INGESTION_VERSION, canonical_json({"direct_issuer_link": True}))
    )


def persist_filing_observation(conn, store, *, run_id, parent_raw_id, retrieval_id,
                               cik, ticker, entity_name, row, retrieved_at,
                               initial_availability_mode):
    accession = str(row.get("accessionNumber") or "").strip()
    form = str(row.get("form") or "").strip()
    acceptance = normalize_timestamp(row.get("acceptanceDateTime"))
    if not accession or not form or not acceptance:
        return False, False

    payload_dict = filing_payload(cik=cik, ticker=ticker, entity_name=entity_name, row=row)
    payload_json = canonical_json(payload_dict)
    payload = payload_json.encode("utf-8")
    digest = hashlib.sha256(payload).hexdigest()
    rel = Path("sec")/"filings"/acceptance[:4]/normalize_cik(cik)/f"{accession}.{digest[:16]}.metadata.json.gz"
    stored = store.write_json(rel, payload)
    normalized_raw_id = hashlib.sha256(
        f"{SOURCE_ID}\0{accession}\0{stored.sha256}".encode()
    ).hexdigest()
    primary = str(row.get("primaryDocument") or "").strip()
    source_url = archive_document_url(cik, accession, primary)
    conn.execute(
        """INSERT OR IGNORE INTO raw_source_documents(
             raw_document_id,source_id,external_id,document_kind,source_url,canonical_url,
             published_at,available_at,retrieved_at,content_type,content_encoding,raw_sha256,
             storage_path,byte_length,parser_status,parser_version,parent_raw_document_id,metadata_json
           ) VALUES(?,?,?,'sec_filing_metadata_normalized_v3',?,?,?,?,?,
             'application/json','gzip',?,?,?,'parsed',?,?,?)""",
        (normalized_raw_id, SOURCE_ID, accession, source_url, source_url, acceptance,
         acceptance, retrieved_at, stored.sha256, str(stored.path), stored.byte_length,
         INGESTION_VERSION, parent_raw_id,
         canonical_json({"availability_source":"acceptanceDateTime",
                         "economic_impact_not_assigned":True,
                         "metadata_versioning_native":True}))
    )
    _link_asset(conn, normalized_raw_id, ticker)

    existing = conn.execute(
        "SELECT raw_document_id FROM sec_filings WHERE accession_number=?", (accession,)
    ).fetchone()
    inserted_identity = existing is None
    stable_id_filing = normalized_raw_id if existing is None else str(existing[0])
    if existing is None:
        conn.execute(
            """INSERT INTO sec_filings(
               raw_document_id,cik,accession_number,form,filing_date,acceptance_datetime,
               report_date,primary_document,primary_doc_description,is_amendment,items_json,
               entity_name,ticker_at_ingestion,metadata_version
               ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?)""",
            (stable_id_filing, normalize_cik(cik), accession, form, row.get("filingDate"),
             acceptance, row.get("reportDate"), primary or None,
             row.get("primaryDocDescription"), int(form.endswith("/A")),
             canonical_json(payload_dict.get("items_normalized", [])),
             entity_name, ticker, INGESTION_VERSION)
        )
    _link_asset(conn, stable_id_filing, ticker)

    version_id = stable_id("sec_filing_metadata_version", accession, digest)
    conn.execute(
        """INSERT OR IGNORE INTO sec_filing_metadata_versions(
           metadata_version_id,filing_raw_document_id,normalized_raw_document_id,
           first_source_submissions_raw_document_id,accession_number,cik,form,filing_date,
           acceptance_datetime,report_date,primary_document,primary_doc_description,is_amendment,
           items_json,entity_name,ticker_at_ingestion,metadata_content_sha256,normalized_metadata_json,
           parser_version,first_observed_at,first_retrieved_at,provenance_status,metadata_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'native',?)""",
        (version_id, stable_id_filing, normalized_raw_id, parent_raw_id, accession, normalize_cik(cik),
         form, row.get("filingDate"), acceptance, row.get("reportDate"), primary or None,
         row.get("primaryDocDescription"), int(form.endswith("/A")),
         canonical_json(payload_dict.get("items_normalized", [])), entity_name, ticker,
         digest, payload_json, INGESTION_VERSION, retrieved_at, retrieved_at,
         canonical_json({"native_016_writer":INGESTION_VERSION}))
    )

    # IMPORTANT: migration 016 may already contain the same metadata content
    # under a migrated canonical version/raw pair. INSERT OR IGNORE above can
    # therefore leave our candidate id/raw unapplied. Resolve the row that
    # actually exists before creating the composite-FK observation.
    version_id, observation_normalized_raw_id = canonical_metadata_version_reference(
        conn,
        filing_raw_document_id=stable_id_filing,
        metadata_content_sha256=digest,
    )

    retrieval_identity = f"{retrieval_id}:{accession}"
    if conn.execute(
        "SELECT 1 FROM sec_filing_metadata_observations WHERE filing_raw_document_id=? AND retrieval_identity=?",
        (stable_id_filing, retrieval_identity)
    ).fetchone():
        return inserted_identity, False

    previous = conn.execute(
        """SELECT metadata_observation_id,metadata_version_id,observation_sequence,state_revision_number
           FROM sec_filing_metadata_observations WHERE filing_raw_document_id=?
           ORDER BY observation_sequence DESC LIMIT 1""",
        (stable_id_filing,)
    ).fetchone()
    prev_id = None if previous is None else str(previous[0])
    prev_version = None if previous is None else str(previous[1])
    prev_seq = 0 if previous is None else int(previous[2])
    prev_rev = None if previous is None else int(previous[3])
    seen = conn.execute(
        "SELECT 1 FROM sec_filing_metadata_observations WHERE filing_raw_document_id=? AND metadata_version_id=? LIMIT 1",
        (stable_id_filing, version_id)
    ).fetchone() is not None
    kind, state_revision = classify_observation(prev_version, prev_rev, version_id, seen)
    seq = prev_seq + 1

    if kind == "initial":
        available_at = acceptance
        basis = "acceptance_datetime_initial"
        pit = int(initial_availability_mode == "live")
    else:
        available_at = retrieved_at
        basis = {"unchanged":"retrieval_time_unchanged",
                 "revision":"retrieval_time_revision",
                 "reversion":"retrieval_time_reversion"}[kind]
        pit = 1

    obs_id = stable_id("sec_filing_metadata_observation", stable_id_filing, retrieval_identity)
    conn.execute(
        """INSERT INTO sec_filing_metadata_observations(
           metadata_observation_id,filing_raw_document_id,metadata_version_id,normalized_raw_document_id,
           source_submission_retrieval_id,source_submissions_raw_document_id,ingestion_run_id,retrieval_identity,
           observation_sequence,state_revision_number,previous_observation_id,observation_kind,observed_at,
           retrieved_at,available_at,availability_basis,availability_is_point_in_time,provenance_status,metadata_json
           ) VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,'native',?)""",
        (obs_id, stable_id_filing, version_id, observation_normalized_raw_id, retrieval_id, parent_raw_id, run_id,
         retrieval_identity, seq, state_revision, prev_id, kind, retrieved_at, retrieved_at,
         available_at, basis, pit,
         canonical_json({"initial_availability_mode":initial_availability_mode,
                         "historical_initial_acceptance_is_proxy":
                             kind=="initial" and initial_availability_mode=="historical",
                         "market_impact_not_assigned":True}))
    )
    return inserted_identity, True


def run_ingestion(*, db, raw_root, tickers, ciks, forms, max_filings,
                  include_older, rate_limit, user_agent, initial_availability_mode):
    run_id = uuid.uuid4().hex
    client = SecClient(validate_user_agent(user_agent), rate_limit_per_second=rate_limit)
    store = RawStore(raw_root)
    mapping = ticker_mapping(client) if tickers else {}
    targets, errors = [], []
    for ticker in tickers:
        rec = mapping.get(ticker.upper())
        if rec is None:
            errors.append({"ticker":ticker,"error":"ticker_not_found_in_sec_mapping"})
        else:
            targets.append(rec)
    for cik in ciks:
        targets.append({"ticker":None,"cik":normalize_cik(cik),"title":None})

    with sqlite3.connect(db) as conn:
        conn.execute("PRAGMA foreign_keys=ON")
        ensure_contract(conn)
        conn.execute(
            "INSERT INTO source_ingestion_runs(run_id,source_id,mode,started_at,status) VALUES(?,?,?,?,'running')",
            (run_id,SOURCE_ID,"historical_metadata_v3" if initial_availability_mode=="historical" else "live_metadata_v3",utc_now())
        )
        conn.commit()
        discovered=identities=observations=0
        for target in targets:
            target_count=0
            try:
                for submission in collect_submission_payloads(
                    client,cik=str(target["cik"]),include_older=include_older
                ):
                    parent_raw, retrieval_id = persist_submission_retrieval(
                        conn,store,run_id=run_id,cik=str(target["cik"]),
                        source_url=submission.source_url,external_id=submission.external_id,
                        storage_name=submission.storage_name,payload=submission.payload,
                        retrieved_at=submission.retrieved_at
                    )
                    entity_name = target["title"] or submission.parsed.get("name")
                    for row in iter_columnar_filings(submission.parsed):
                        if str(row.get("form") or "") not in forms:
                            continue
                        if max_filings is not None and target_count >= max_filings:
                            break
                        target_count += 1; discovered += 1
                        ins, obs = persist_filing_observation(
                            conn,store,run_id=run_id,parent_raw_id=parent_raw,
                            retrieval_id=retrieval_id,cik=str(target["cik"]),
                            ticker=str(target["ticker"]) if target["ticker"] else None,
                            entity_name=str(entity_name) if entity_name else None,row=row,
                            retrieved_at=submission.retrieved_at,
                            initial_availability_mode=initial_availability_mode
                        )
                        identities += int(ins); observations += int(obs)
                    conn.commit()
                    if max_filings is not None and target_count >= max_filings:
                        break
            except Exception as exc:
                conn.rollback()
                errors.append({"ticker":target.get("ticker"),"cik":target.get("cik"),"error":str(exc)})

        status = "completed" if not errors else "completed_with_errors"
        conn.execute(
            """UPDATE source_ingestion_runs SET finished_at=?,status=?,documents_discovered=?,
               documents_inserted=?,documents_existing=?,error_count=?,error_json=? WHERE run_id=?""",
            (utc_now(),status,discovered,identities,max(0,discovered-identities),
             len(errors),canonical_json(errors),run_id)
        )
        conn.commit()
    return {"run_id":run_id,"status":status,"documents_discovered":discovered,
            "filing_identities_inserted":identities,
            "metadata_observations_written":observations,"errors":errors}


def main():
    p=argparse.ArgumentParser()
    p.add_argument("--ticker",action="append",default=[])
    p.add_argument("--cik",action="append",default=[])
    p.add_argument("--forms",default=",".join(DEFAULT_FORMS))
    p.add_argument("--max-filings",type=int,default=30)
    p.add_argument("--include-older",action="store_true")
    p.add_argument("--initial-availability-mode",choices=("historical","live"),default="historical")
    p.add_argument("--db",type=Path,default=DEFAULT_DB)
    p.add_argument("--raw-root",type=Path,default=DEFAULT_RAW_ROOT)
    p.add_argument("--rate-limit",type=float,default=5.0)
    p.add_argument("--user-agent")
    a=p.parse_args()
    if not a.ticker and not a.cik: raise SystemExit("Indicá --ticker o --cik")
    ua=a.user_agent or os.environ.get("SEC_USER_AGENT")
    result=run_ingestion(db=a.db,raw_root=a.raw_root,tickers=a.ticker,ciks=a.cik,
        forms=parse_forms(a.forms),max_filings=a.max_filings,include_older=a.include_older,
        rate_limit=a.rate_limit,user_agent=validate_user_agent(ua),
        initial_availability_mode=a.initial_availability_mode)
    print(json.dumps(result,indent=2,ensure_ascii=False))


if __name__=="__main__":
    main()

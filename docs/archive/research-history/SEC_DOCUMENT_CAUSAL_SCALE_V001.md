# Causal SEC document scale — next step

Your metadata scale is healthy: 276 filings / 521 metadata observations / 245
retrieval-time PIT observations.

Before clustering those filings, this package closes two causal gaps:

1. `sec_filing_documents_v2` selects the metadata version available as-of the
   document-ingestion run start and records the exact metadata observation used.

2. The current deterministic clustering code marks all SEC raw documents as
   `availability_is_point_in_time=True`. The patch changes this:
   - if document availability is backfilled to SEC acceptance time, inherit the
     initial filing-metadata PIT flag (historical research backfill = 0);
   - if the raw document is a revision first observed at retrieval time, PIT = 1.

No migration is added.

## Commands

```bash
unzip -o quant_market_ai_sec_documents_causal_scale_v001.zip -d ~/quant_market_ai
cd ~/quant_market_ai

python tools/patch_deterministic_clustering_sec_pit.py

python -m py_compile   ingestion/events/sec_filing_documents_v2.py   pipeline/event_brain_documents_v001.py   ingestion/events/deterministic_clustering.py

python -m pytest   tests/test_sec_documents_v2_contract.py   tests/test_deterministic_event_clustering.py   tests/test_sec_filing_documents.py   -q
```

Audit current coverage:

```bash
python -m pipeline.event_brain_documents_v001 --stage audit
```

Then download in bounded batches:

```bash
export SEC_USER_AGENT="QuantMarketAI/0.4 joaquinhazzi@gmail.com"

python -m pipeline.event_brain_documents_v001 --stage documents
```

Audit again:

```bash
python -m pipeline.event_brain_documents_v001 --stage audit
```

After that we will cluster per ticker using the actual raw-document counts,
normalize events, rebuild event states and reaction labels, and train the first
real Event Brain if the diversity gates pass.

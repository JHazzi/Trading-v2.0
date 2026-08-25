# Event Brain data scaling — next commands

Copy this package over the repo.

Validate:

```bash
python -m py_compile \
  ingestion/events/sec_edgar_v3.py \
  pipeline/event_brain_scale_v001.py

python -m pytest \
  tests/test_sec_metadata_v3_contract.py \
  tests/test_event_brain_v001_contract.py \
  -q
```

Preflight:

```bash
python -m pipeline.event_brain_scale_v001 --stage preflight
```

Then ingest 10-year daily prices:

```bash
python -m pipeline.event_brain_scale_v001 --stage prices
```

Set the SEC user agent:

```bash
export SEC_USER_AGENT="QuantMarketAI/0.4 joaquinhazzi@gmail.com"
```

Then ingest bounded historical SEC metadata with native migration-016 lineage:

```bash
python -m pipeline.event_brain_scale_v001 --stage sec-metadata
```

Finally audit:

```bash
python -m pipeline.event_brain_scale_v001 --stage audit
```

Send the outputs of preflight, SEC metadata and audit back before downloading
hundreds of filing documents. We will size the document batches and clustering
limit from the real counts instead of guessing.

Do not lower Event Brain's min_rows gate.

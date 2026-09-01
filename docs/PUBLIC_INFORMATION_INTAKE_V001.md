# Public Information Intake V001

**Status:** full bars and priority news downloaded; structural intake and read-only semantics audit PASS; canonicalization/model visibility blocked
**Scientific scope:** acquisition and structural audit only; no feature materialization or training

## Purpose

This track acquires the two information blocks identified by Information
Integration Readiness V001 as materially missing:

- historical one-minute equity bars;
- historical non-SEC financial news.

It does not extend, replace or repair V009. It never opens or writes
`market_data_v2.db`, Market Core or the V009 registry. Raw downloads live in a
new quarantine tree, and a small catalog stores immutable source revision,
file identity, rights status, local path, checksums, runs and audits.

## Hard boundaries

The configuration freezes these rules:

```text
training_authorized = false
feature_visibility  = blocked
maximum managed storage = 100 GiB
space preallocation = false
cross-source price median = forbidden
source observation overwrite = forbidden
volume blending across IEX/SIP/Yahoo = forbidden
```

The 100 GiB value is a ceiling over the new raw and lake roots, not reserved
space. Every download computes the current managed footprint, resumable bytes
remaining and filesystem free-space margin before transferring bytes.

## Storage architecture

```text
data/database/public_information_catalog_v001.db
    metadata/catalog only (SQLite)

data/raw/public_information_v001/
    immutable remote snapshot manifests
    shared objects by dataset + resolved commit
    original Parquet files and resumable .part files

data/lake/public_information_v001/
    small audited samples and later derived source-preserving partitions

reports/public_information_intake_v001/
    append-only timestamped plan/manifest/download/audit/sample reports
```

Large Parquet files are not copied into `market_data_v2.db`. DuckDB reads them
in place and can later expose source-specific views without creating hundreds
of millions of SQLite rows.

## Dataset profiles

### Alpaca-derived Hugging Face bars

`alpaca_hf_bars_1m/full` is fixed to commit
`b21d46e47ea2f39801d174ca850af76999cc5113`. The dataset card declares MIT,
but the upstream vendor redistribution basis remains unverified. It is local
research evidence only until provenance/feed review establishes more.

The feed is deliberately recorded as unknown. Alpaca SIP and IEX are different
statistical objects; a filename or dataset description is insufficient to
infer which was used.

### Multi-source news

`financial_news_multisource/priority` initially selects five finance-relevant
subsets:

```text
fnspid_news
yahoo_finance_felixdrinkall
cnbc_headlines
finsen_us_2007_2023
reddit_finance_sp500
```

The configured `main` ref is resolved to an immutable commit by the manifest
stage. The corpus remains research-only with unresolved publisher rights,
cross-subset duplicates and mixed timestamp precision. A day-level or derived
timestamp is never treated as exact intraday availability.

The `full` profile exists but should be frozen/downloaded only after reviewing
the priority audit. This sequencing is a falsifiability and rights gate, not a
storage limitation.

## Idempotence and interruption behavior

- A remote tree is converted into an immutable content-addressed manifest.
- Repeating `manifest` for the same selection reuses the same snapshot.
- Priority/full profiles at the same commit reuse identical object bytes rather
  than downloading the same Parquet twice.
- Downloads stream in 8 MiB chunks and never load a dataset into memory.
- Interrupted bytes remain in `.part` and resume with HTTP Range.
- If the server refuses Range, the partial file is preserved and the run
  blocks rather than silently restarting or deleting evidence.
- A completed file is skipped after exact-size and available SHA-256 checks.
- A per-snapshot lock prevents concurrent writers.
- Tokens are read only from `HF_TOKEN`; they are never printed or persisted.

## Audit meanings

```text
integrity  local existence and exact file size
metadata   integrity + Parquet schema + row count
full       one-pass structural content metrics
sample     small source-preserving Parquet for detailed next-stage review
```

A PASS means only that raw bytes/schema/declared structural constraints are
coherent. News remains blocked on rights, timestamp and deduplication review.
Bars remain blocked on feed, session, adjustment, ticker-history and opening
semantics review. No PASS from this tool authorizes training.

## Exact first execution

From the repository root:

```bash
python -m py_compile \
  ingestion/public_information/intake_v001.py \
  pipeline/public_information_intake_v001.py

python -m unittest \
  tests.test_public_information_intake_v001 -v

python -m pipeline.public_information_intake_v001 --stage plan
python -m pipeline.public_information_intake_v001 --stage init
```

Freeze and inspect the exact Alpaca-derived source before downloading:

```bash
python -m pipeline.public_information_intake_v001 \
  --stage manifest \
  --dataset alpaca_hf_bars_1m \
  --profile full

python -m pipeline.public_information_intake_v001 \
  --stage download \
  --dataset alpaca_hf_bars_1m \
  --profile full \
  --dry-run
```

If the dry run passes, start/resume the actual transfer:

```bash
python -m pipeline.public_information_intake_v001 \
  --stage download \
  --dataset alpaca_hf_bars_1m \
  --profile full
```

Install the isolated Parquet query dependency only after the raw transfer:

```bash
python -m pip install -r requirements-information-intake.txt
```

Then run progressively more expensive checks:

```bash
python -m pipeline.public_information_intake_v001 \
  --stage audit --dataset alpaca_hf_bars_1m --profile full \
  --audit-level integrity

python -m pipeline.public_information_intake_v001 \
  --stage audit --dataset alpaca_hf_bars_1m --profile full \
  --audit-level metadata

python -m pipeline.public_information_intake_v001 \
  --stage sample --sample-kind bars
```

`--audit-level full` scans the approximately 604 million rows and is deferred
until the inexpensive audit/sample reports are reviewed.

For priority news, create a Hugging Face account/access token if the repository
requires it. Export the token in the shell without placing it in `.env` or a
command-line argument:

```bash
export HF_TOKEN='your_token_here'

python -m pipeline.public_information_intake_v001 \
  --stage manifest \
  --dataset financial_news_multisource \
  --profile priority

python -m pipeline.public_information_intake_v001 \
  --stage download \
  --dataset financial_news_multisource \
  --profile priority \
  --dry-run

python -m pipeline.public_information_intake_v001 \
  --stage download \
  --dataset financial_news_multisource \
  --profile priority

python -m pipeline.public_information_intake_v001 \
  --stage audit --dataset financial_news_multisource --profile priority \
  --audit-level metadata

python -m pipeline.public_information_intake_v001 \
  --stage sample --sample-kind news
```

Do not share the token or raw publisher text. Return the generated plan,
manifest, integrity/metadata audit and sample reports for review.

## Next gate after successful raw intake

The next version is not a model. It is a source-semantics audit:

1. identify Alpaca feed and adjustment/session semantics;
2. map ticker validity intervals to stable asset identities;
3. compare minute-derived RTH daily bars against Yahoo without overwriting;
4. audit official open, first eligible trade, first minute and first-five-minute
   VWAP semantics;
5. parse news `extra_fields`, classify timestamp precision and source rights;
6. exact/URL/near-duplicate clustering while retaining dissemination count;
7. only then design a separate point-in-time information-state materializer.

## Post-download semantic checkpoint

The frozen full bars and priority-news snapshots were downloaded and passed
metadata/schema review. Public Information Semantics Audit V001 subsequently
completed read-only with input state unchanged. See
`PUBLIC_INFORMATION_SEMANTICS_AUDIT_V001.md` for real coverage, source/time,
deduplication, identity and session findings. Raw acquisition is complete for
this scope; canonicalization and model visibility remain blocked.

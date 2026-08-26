# Relation Evidence V002 EX-21 header patch helper — V002 fix

The previous helper searched for a source-code literal of the form:

```text
r"\bwhere\s+incorporated\b"
```

but the actual V002 extractor stores that regex alternative inside the
multiline `BAD_NAME_PHRASES = re.compile(r"""...""")` block:

```text
|\bwhere\s+incorporated\b
```

Therefore the old helper could never find its anchor.

This helper:

1. structurally locates `BAD_NAME_PHRASES`;
2. verifies the expected `quality_flags` context;
3. checks whether `organized or incorporated` is already present;
4. requires the existing `where incorporated` alternative;
5. inserts the new rule immediately after it;
6. validates the resulting source;
7. is idempotent.

This is a source hygiene fix only. It does not change the already-built
Relation Evidence V002 database and does not require rerunning extraction for
Entity Registry V001, because the registry independently rejects the known
headers.

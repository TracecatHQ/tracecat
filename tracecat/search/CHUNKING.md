# Resumable text chunking

`TextChunker` prepares bounded labeled text for an embedding client. It performs
no database writes, provider calls or Temporal operations. One row remains one
document; selected TEXT columns are processed separately, sorted by column UUID.

## Caller contract

Construct a chunker with a `ChunkingIdentity`, selected `TextColumn` metadata,
`ChunkingConfig`, and a local `TokenCounter`. The caller validates that selected
columns are TEXT and resolves their current names. A token counter's identity
must include its version and options, including special-token handling.

Call `await chunker.prepare_batch(reader, checkpoint)`. `SourceReader.read_slice`
receives the build identity, column ID, start offset and maximum character count.
It must check the source revision in the same consistent read as the slice.
Missing/deleted/stale sources are errors; a null value is an empty final slice.
Return exactly the requested character count unless the column ends sooner,
and report the column's end explicitly. A short non-final slice is rejected.
The reader must read bounded slices directly, not load a column and slice it in
Python. PostgreSQL substring adapters translate the zero-based start to a
one-based position.

Each `PreparedChunk` contains the labeled `text` and `ChunkMetadata`: caller
identity, configuration hash, column ID/name, document-wide ordinal, source
start/end, token count and SHA-256 of the UTF-8 labeled input. Offsets count
Unicode code points in the original text, start inclusive and end exclusive;
they are not byte or UTF-16 offsets. Combining marks and emoji are preserved as
source characters, but grapheme clusters need not stay within one chunk.

The label is the column name followed by a colon and newline. Normalization is
identity (`none`). Whole whitespace-only windows are skipped; every nonblank
source character is covered. Original offsets include any whitespace retained
within an emitted chunk.

## Sizes and splitting

The target is 800 total tokens including the label, with 128 tokens of overlap.
The effective input limit is the smaller of the target and provider limit.
Overlap is further limited to half the input budget, space after the label,
and half a character window. It is dropped if needed to fit new text.

Read windows default to 4,096 characters. Count the entire labeled candidate,
then search for a fitting character prefix. Prefer paragraph/sentence endings
in the latter half of that prefix when their exact labeled input also fits.
Every chosen prefix/suffix is counted: token counts are not assumed to increase
monotonically with string length, and the search does not promise the largest
possible fitting prefix. If the full candidate does not fit and even the next
required character cannot fit, raise `InvalidChunkingConfig`.

Character windows may yield inputs below the token target for highly
compressible text. The character bound remains necessary even with a generous
token budget. Only valid Unicode character boundaries are used; encoded token
bytes are never sliced. Each step covers new characters or finishes a column.

## Checkpoints and completion

Persist the returned chunk manifests and checkpoint **in one transaction**.
Serialize with `checkpoint.model_dump_json()` (or `model_dump(mode="json")`),
and restore with `ChunkCheckpoint.model_validate_json(...)`. JSON validation
rejects unknown versions, extra fields and invalid offsets. The checkpoint is
text-free and contains:

- Version, caller build identity and configuration hash.
- `column_index`: the position in the sorted column list.
- `character_offset`: end of the source already covered or skipped as blank.
- `overlap_start`: start of the next read; overlap is reconstructed from source.
- `next_ordinal`: the next document-wide chunk number.

The configuration hash covers all chunking settings and sorted column IDs/names.
Changed source revisions, builds, labels, selections or settings cannot resume
an old checkpoint. A new lease for the same build can resume it. Treat persisted
checkpoints as trusted internal state, not user-supplied cursors.

A batch returns at most 32 chunks and reads at most 32 windows. Callers may lower
either limit without changing chunk boundaries. A batch with zero chunks can
still be incomplete (for example after scanning a long blank column). Continue
from its checkpoint. Only `complete=True` supplies the final
`expected_chunk_count`, including zero for a blank document. Completion of
enumeration does not mark the row Ready.

Use `reconstruct_input(reader, reference)` to reread one saved range and verify
the exact labeled input/hash and provider budget. `ChunkReference` needs no
persisted token count. Passing the richer `ChunkMetadata` also verifies its
saved token count. The caller supplies the original build identity and pinned
configuration; the source reader must still check that revision.

## Storage/provider handoff

These local types deliberately do not import the not-yet-merged storage PR's
`types.py`. Map `ChunkingIdentity` from `SearchScope` and `BuildClaim`, excluding
the lease fence: fencing is the worker/storage responsibility. Map metadata's
ordinal, column ID/name, offsets and input hash to the existing `ChunkManifest`.
Existing storage can omit token counts because reconstruction recounts them.

The initial storage `EnumerationCursor` has only column index, character offset
and next ordinal. It must preserve the **full versioned checkpoint** when wired
to this chunker, including `overlap_start` and identity/configuration binding.
Do not reduce a checkpoint to those three fields or default missing overlap
state on a partial build. This is a coordinated storage/worker interface change;
this module does not edit sibling migrations or storage services.

Persist `provider_input_tokens`, `read_size` and `max_label_characters` with the
existing pinned chunker settings (or bind their versioned defaults immutably).
Provider compatibility tests should exercise the provider's exact local
tokenizer and limits once that adapter lands. Source read consistency, atomic
manifest/checkpoint persistence, and publication are worker integration tests.

## Focused validation

```bash
uv run pytest --confcutdir=tests/unit tests/unit/test_search_chunking.py
uv run ruff check tracecat/search tests/unit/test_search_chunking.py
uv run ruff format --check tracecat/search tests/unit/test_search_chunking.py
uv run basedpyright tracecat/search tests/unit/test_search_chunking.py
```

The focused pytest command excludes service-wide fixtures: these pure tests do
not need PostgreSQL, Temporal, Redis, or MinIO. Generated slice fixtures cover
thousands of chunks without constructing the whole long source, and count read
sizes/tokenizer calls to check the work bounds. Resume tests compare exact
chunks and checkpoints across retries and different batch limits.

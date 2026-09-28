"""Pure chunker tests: production code only receives bounded source slices."""

import asyncio
import hashlib
import math
import threading
from dataclasses import dataclass, replace
from uuid import UUID

import pytest
from pydantic import ValidationError

from tracecat.search.chunking import MAX_PREFIX_PROBES, TextChunker
from tracecat.search.chunking_types import (
    ChunkBatch,
    ChunkCheckpoint,
    ChunkingConfig,
    ChunkingIdentity,
    ChunkInputMismatch,
    ChunkReference,
    InvalidCheckpoint,
    InvalidChunkingConfig,
    InvalidSourceSlice,
    PreparedChunk,
    SourceSlice,
    TextColumn,
)

pytestmark = pytest.mark.anyio


@pytest.fixture
def anyio_backend() -> str:
    return "asyncio"


IDENTITY = ChunkingIdentity(
    organization_id=UUID(int=1),
    workspace_id=UUID(int=2),
    collection_id=UUID(int=3),
    document_id=UUID(int=4),
    generation=1,
    config_version=1,
    revision=1,
)
COLUMN = TextColumn(id=UUID(int=10), name="description")


class ByteCounter:
    """Deterministic test tokenizer; Unicode characters have different costs."""

    identity = "test-utf8-v1"

    def __init__(self) -> None:
        self.calls = 0
        self.max_characters = 0

    def count_tokens(self, text: str) -> int:
        self.calls += 1
        self.max_characters = max(self.max_characters, len(text))
        return len(text.encode("utf-8"))


@dataclass(frozen=True, slots=True)
class PatternSource:
    """Generate arbitrary-size test data without allocating the whole column."""

    pattern: str
    repetitions: int = 1

    @property
    def length(self) -> int:
        return len(self.pattern) * self.repetitions

    def slice(self, start: int, limit: int) -> str:
        size = min(limit, max(0, self.length - start))
        if not size:
            return ""
        offset = start % len(self.pattern)
        repeats = math.ceil((offset + size) / len(self.pattern))
        return (self.pattern * repeats)[offset : offset + size]


class PatternReader:
    def __init__(self, sources: dict[UUID, PatternSource | None]) -> None:
        self.sources = sources
        self.calls = 0
        self.max_limit = 0
        self.identity = IDENTITY

    async def read_slice(
        self, identity: ChunkingIdentity, column_id: UUID, start: int, limit: int
    ) -> SourceSlice:
        assert identity == self.identity, "The caller must check the source revision"
        self.calls += 1
        self.max_limit = max(self.max_limit, limit)
        source = self.sources[column_id]
        length = source.length if source is not None else 0
        if start > length:
            raise InvalidSourceSlice("Read starts beyond the source revision")
        text = source.slice(start, limit) if source is not None else ""
        return SourceSlice(text=text, end_of_column=start + len(text) == length)


def config(*, budget: int = 64, read_size: int = 128) -> ChunkingConfig:
    return ChunkingConfig(
        tokenizer=ByteCounter.identity,
        input_tokens=800,
        overlap_tokens=128,
        provider_input_tokens=budget,
        read_size=read_size,
    )


def chunker(
    settings: ChunkingConfig | None = None,
    *,
    columns: tuple[TextColumn, ...] = (COLUMN,),
    counter: ByteCounter | None = None,
    identity: ChunkingIdentity = IDENTITY,
) -> TextChunker:
    return TextChunker(
        identity=identity,
        columns=columns,
        config=settings if settings is not None else config(),
        tokenizer=counter if counter is not None else ByteCounter(),
    )


async def collect(
    splitter: TextChunker,
    reader: PatternReader,
    checkpoint: ChunkCheckpoint | None = None,
    *,
    max_chunks: int = 32,
    max_windows: int = 32,
) -> tuple[list[PreparedChunk], list[ChunkBatch]]:
    chunks: list[PreparedChunk] = []
    batches: list[ChunkBatch] = []
    for _ in range(20000):
        batch = await splitter.prepare_batch(
            reader, checkpoint, max_chunks=max_chunks, max_windows=max_windows
        )
        assert len(batch.chunks) <= max_chunks
        chunks.extend(batch.chunks)
        batches.append(batch)
        if batch.complete:
            assert batch.expected_chunk_count == batch.checkpoint.next_ordinal
            return chunks, batches
        assert batch.expected_chunk_count is None
        assert batch.checkpoint != checkpoint
        checkpoint = batch.checkpoint
    pytest.fail("Chunker did not finish within the test's bounded iteration guard")


def assert_coverage(chunks: list[PreparedChunk], reader: PatternReader) -> None:
    assert [chunk.metadata.ordinal for chunk in chunks] == list(range(len(chunks)))
    for column_id, source in reader.sources.items():
        covered = 0
        for chunk in chunks:
            metadata = chunk.metadata
            if metadata.column_id != column_id:
                continue
            assert source is not None
            assert metadata.start >= 0
            assert metadata.end > covered
            if metadata.start > covered:
                assert not source.slice(covered, metadata.start - covered).strip()
            assert metadata.end <= source.length
            expected = f"{metadata.column_name}:\n" + source.slice(
                metadata.start, metadata.end - metadata.start
            )
            assert chunk.text == expected
            assert hashlib.sha256(expected.encode()).hexdigest() == metadata.input_hash
            assert metadata.token_count == len(expected.encode())
            covered = metadata.end
        if source is not None:
            assert not source.slice(covered, source.length - covered).strip()


@pytest.mark.parametrize(
    "text",
    [
        "",
        " \t\r\n" * 200,
        "abc" * 500,
        "Paragraph one. More words.\n\nParagraph two!\r\n\r\n" * 30,
        "你好世界🙂 👨‍👩‍👧‍👦 e\u0301 العربية\n" * 100,
        "x" + " " * 800 + "y",
        " " * 800 + "y" + " " * 800,
        "\x00line\r\nnext\n" * 30,
    ],
    ids=[
        "empty",
        "blank",
        "unbroken",
        "paragraphs",
        "unicode",
        "blank-gap",
        "padding",
        "newlines",
    ],
)
async def test_source_coverage_hashes_and_unicode(text: str) -> None:
    reader = PatternReader({COLUMN.id: PatternSource(text)})
    splitter = chunker()
    chunks, batches = await collect(splitter, reader)
    assert_coverage(chunks, reader)
    assert all(chunk.metadata.token_count <= 64 for chunk in chunks)
    for chunk in chunks:
        assert await splitter.reconstruct_input(reader, chunk.metadata) == chunk.text
    assert batches[-1].expected_chunk_count == len(chunks)
    assert reader.max_limit <= 128


async def test_column_order_empty_values_and_no_cross_column_overlap() -> None:
    columns = tuple(
        TextColumn(id=UUID(int=20 + i), name=f"field_{i}") for i in range(5)
    )
    reader = PatternReader(
        {
            columns[0].id: None,
            columns[1].id: PatternSource("one two three ", 10),
            columns[2].id: PatternSource(""),
            columns[3].id: PatternSource(" \n", 200),
            columns[4].id: PatternSource("the last field", 10),
        }
    )
    chunks, _ = await collect(chunker(columns=columns[::-1]), reader)
    assert_coverage(chunks, reader)
    ids = [chunk.metadata.column_id for chunk in chunks]
    assert ids == sorted(ids)
    assert set(ids) == {columns[1].id, columns[4].id}
    for column in (columns[1], columns[4]):
        first = next(chunk for chunk in chunks if chunk.metadata.column_id == column.id)
        assert first.metadata.start == 0


async def test_resume_every_batch_matches_uninterrupted_and_serialization() -> None:
    reader = PatternReader(
        {COLUMN.id: PatternSource("First sentence.\n\nNext 🙂 é. ", 90)}
    )
    expected, batches = await collect(chunker(), reader, max_chunks=7, max_windows=9)
    for batch in batches:
        saved = batch.checkpoint.model_dump_json()
        cursor = ChunkCheckpoint.model_validate_json(saved)
        assert cursor == batch.checkpoint
        assert "First sentence" not in saved
        resumed, _ = await collect(
            chunker(), reader, cursor, max_chunks=3, max_windows=5
        )
        assert resumed == expected[cursor.next_ordinal :]
    repeated, retry_batches = await collect(
        chunker(), reader, max_chunks=7, max_windows=9
    )
    assert repeated == expected
    assert retry_batches == batches


async def test_window_and_chunk_batch_limits_do_not_change_boundaries() -> None:
    reader = PatternReader({COLUMN.id: PatternSource("a sentence. more.\n\n", 50)})
    reference, _ = await collect(chunker(), reader)
    for limit in (1, 2, 7, 32):
        actual, _ = await collect(
            chunker(), reader, max_chunks=limit, max_windows=limit
        )
        assert actual == reference


async def test_large_source_is_generated_and_read_in_bounded_work_units() -> None:
    counter = ByteCounter()
    settings = config(budget=800, read_size=4096)
    splitter = chunker(settings, counter=counter)
    # More than 3,000 chunks; the fixture never materializes the whole source.
    source = PatternSource("long unbroken unicode string 🙂", 100000)
    reader = PatternReader({COLUMN.id: source})
    cursor = None
    covered = ordinal = batches = 0
    saved: list[ChunkCheckpoint] = []
    while True:
        before_reads, before_counts = reader.calls, counter.calls
        batch = await splitter.prepare_batch(reader, cursor)
        assert reader.calls - before_reads <= 32
        assert counter.calls - before_counts <= 32 * (
            2 * math.ceil(math.log2(4096)) + 10
        )
        assert len(batch.chunks) <= 32
        for chunk in batch.chunks:
            metadata = chunk.metadata
            assert metadata.ordinal == ordinal
            assert metadata.start <= covered < metadata.end
            assert metadata.token_count <= 800
            assert await splitter.reconstruct_input(reader, metadata) == chunk.text
            covered = metadata.end
            ordinal += 1
        if cursor is not None:
            saved.append(cursor)
        batches += 1
        if batch.complete:
            assert batch.expected_chunk_count == ordinal
            break
        assert batch.expected_chunk_count is None
        cursor = ChunkCheckpoint.model_validate_json(batch.checkpoint.model_dump_json())
    assert ordinal > 3000
    assert batches > 100
    assert covered == source.length
    assert reader.max_limit == 4096
    assert counter.max_characters <= 4096 + len(COLUMN.name) + 2
    assert max(len(cursor.model_dump_json()) for cursor in saved) < 1024
    # Replaying a saved batch uses exactly the same manifests and next checkpoint.
    for cursor in saved[::20]:
        original = await splitter.prepare_batch(reader, cursor)
        recreated = await chunker(settings).prepare_batch(reader, cursor)
        assert recreated == original


async def test_blank_text_and_empty_columns_yield_without_false_completion() -> None:
    counter = ByteCounter()
    reader = PatternReader({COLUMN.id: PatternSource(" ", 1000000)})
    batch = await chunker(counter=counter).prepare_batch(reader)
    assert not batch.complete and batch.chunks == ()
    assert batch.checkpoint.character_offset == 32 * 128
    assert reader.calls == 32
    assert counter.calls == 0
    columns = tuple(TextColumn(id=UUID(int=i + 100), name="empty") for i in range(1000))
    reader = PatternReader({column.id: None for column in columns})
    splitter = chunker(columns=columns)
    first = await splitter.prepare_batch(reader)
    assert first.checkpoint.column_index == 32
    assert not first.complete and not first.chunks
    chunks, batches = await collect(splitter, reader, first.checkpoint)
    assert not chunks and batches[-1].expected_chunk_count == 0


async def test_empty_document_completion_is_repeatable() -> None:
    reader = PatternReader({})
    splitter = chunker(columns=())
    result = await splitter.prepare_batch(reader)
    assert result.complete and result.expected_chunk_count == 0
    assert await splitter.prepare_batch(reader, result.checkpoint) == result
    assert reader.calls == 0


@pytest.mark.parametrize("extra_budget", [1, 2, 4, 8, 16, 32])
async def test_small_valid_budgets_preserve_progress(extra_budget: int) -> None:
    budget = len(f"{COLUMN.name}:\n") + extra_budget
    reader = PatternReader({COLUMN.id: PatternSource("abcdef", 40)})
    chunks, _ = await collect(chunker(config(budget=budget)), reader)
    assert_coverage(chunks, reader)
    assert all(chunk.metadata.token_count <= budget for chunk in chunks)


async def test_overlap_is_reduced_when_next_character_is_more_expensive() -> None:
    settings = config(budget=18)
    reader = PatternReader({COLUMN.id: PatternSource("abcdefghijk🙂🙂abcdefghij", 10)})
    chunks, _ = await collect(chunker(settings), reader)
    assert_coverage(chunks, reader)
    assert all(chunk.metadata.token_count <= 18 for chunk in chunks)


@pytest.mark.parametrize("budget", [1, 12, 13, 16])
async def test_too_small_budget_returns_explicit_safe_error(budget: int) -> None:
    reader = PatternReader({COLUMN.id: PatternSource("🙂 private synthetic text")})
    with pytest.raises(InvalidChunkingConfig) as error:
        await chunker(config(budget=budget)).prepare_batch(reader)
    assert "private" not in str(error.value)
    assert "🙂" not in str(error.value)


async def test_default_size_overlap_and_boundary_preference() -> None:
    reader = PatternReader({COLUMN.id: PatternSource("x", 2000)})
    settings = config(budget=1000, read_size=4096)
    chunks, _ = await collect(chunker(settings), reader)
    assert chunks[0].metadata.token_count == 800
    assert chunks[0].metadata.end - chunks[1].metadata.start == 128
    for text, expected_end in [
        ("a" * 30 + "\n\n" + "b" * 80, 32),
        ("a" * 30 + ". " + "b" * 80, 31),
    ]:
        reader = PatternReader({COLUMN.id: PatternSource(text)})
        batch = await chunker().prepare_batch(reader, max_chunks=1)
        assert batch.chunks[0].metadata.end == expected_end


class MergingCounter(ByteCounter):
    """Adding a character can merge tokens and lower the total count."""

    def count_tokens(self, text: str) -> int:
        super().count_tokens(text)
        return len(text) if len(text) % 7 else 1


class PairMergingCounter(ByteCounter):
    """A Unicode pair costs one token; its first character alone costs ten."""

    def count_tokens(self, text: str) -> int:
        super().count_tokens(text)
        return len(text.replace("你好", "x").replace("你", "x" * 10).encode())


async def test_later_prefix_fits_when_first_character_and_full_window_do_not() -> None:
    counter = PairMergingCounter()
    reader = PatternReader({COLUMN.id: PatternSource("你好", 5)})
    splitter = chunker(config(budget=14), counter=counter)
    chunks, _ = await collect(splitter, reader)
    assert [chunk.text for chunk in chunks] == ["description:\n你好"] * 5
    assert [chunk.metadata.end for chunk in chunks] == [2, 4, 6, 8, 10]
    for chunk in chunks:
        assert chunk.metadata.token_count == 14
        assert await splitter.reconstruct_input(reader, chunk.metadata) == chunk.text


async def test_later_prefix_fits_after_overlap_is_discarded() -> None:
    class WidePairMergingCounter(ByteCounter):
        def count_tokens(self, text: str) -> int:
            super().count_tokens(text)
            return len(text.replace("你好", "xxxx").replace("你", "x" * 10).encode())

    counter = WidePairMergingCounter()
    reader = PatternReader({COLUMN.id: PatternSource("abcde你好", 4)})
    splitter = chunker(config(budget=18), counter=counter)
    first = await splitter.prepare_batch(reader, max_chunks=1)
    assert first.checkpoint.character_offset == 5
    assert first.checkpoint.overlap_start < first.checkpoint.character_offset
    resumed, batches = await collect(splitter, reader, first.checkpoint, max_windows=1)
    assert resumed[0].metadata.start == 5
    assert resumed[0].text.startswith("description:\n你好")
    expected, _ = await collect(splitter, reader)
    assert list(first.chunks) + resumed == expected
    assert any(
        not batch.chunks
        and batch.checkpoint.overlap_start == batch.checkpoint.character_offset
        and batch.checkpoint.next_prefix_length == 0
        for batch in batches
    )
    for batch in batches:
        restored = ChunkCheckpoint.model_validate_json(
            batch.checkpoint.model_dump_json()
        )
        recreated = chunker(config(budget=18), counter=WidePairMergingCounter())
        replay, _ = await collect(recreated, reader, restored)
        assert replay == expected[restored.next_ordinal :]
    covered = 0
    for chunk in expected:
        assert chunk.metadata.start <= covered < chunk.metadata.end
        assert chunk.metadata.token_count <= 18
        covered = chunk.metadata.end
    assert covered == 28


async def test_searches_retained_overlap_before_discarding_it() -> None:
    class ContextMergingCounter(ByteCounter):
        def count_tokens(self, text: str) -> int:
            counted = super().count_tokens(text)
            return 1 if text == "description:\na你好" else counted

    reader = PatternReader({COLUMN.id: PatternSource("aa你好")})
    splitter = chunker(config(budget=15), counter=ContextMergingCounter())
    first = await splitter.prepare_batch(reader, max_chunks=1)
    assert first.chunks[0].text == "description:\naa"
    assert first.checkpoint.character_offset == 2
    assert first.checkpoint.overlap_start == 1
    resumed = await splitter.prepare_batch(reader, first.checkpoint)
    assert resumed.complete
    assert resumed.chunks[0].text == "description:\na你好"
    assert resumed.chunks[0].metadata.token_count == 1
    assert resumed.chunks[0].metadata.start == 1
    assert resumed.chunks[0].metadata.end == 4


async def test_prefix_search_yields_and_resumes_without_restarting_probes() -> None:
    class SparseFitCounter(ByteCounter):
        def count_tokens(self, text: str) -> int:
            counted = super().count_tokens(text)
            return 1 if text == "description:\n" + "a" * 63 else counted

    settings = config(budget=1, read_size=256)
    reader = PatternReader({COLUMN.id: PatternSource("a", 126)})
    counter = SparseFitCounter()
    splitter = chunker(settings, counter=counter)
    first = await splitter.prepare_batch(reader, max_windows=1)
    assert first.chunks == () and not first.complete
    assert first.checkpoint.character_offset == 0
    assert first.checkpoint.next_ordinal == 0
    assert first.checkpoint.next_prefix_length == 2 + MAX_PREFIX_PROBES
    assert counter.calls <= MAX_PREFIX_PROBES + 3

    expected, batches = await collect(splitter, reader, max_windows=1)
    assert [chunk.metadata.end for chunk in expected] == [63, 126]
    assert (
        batches[1].checkpoint.next_prefix_length > first.checkpoint.next_prefix_length
    )
    for batch in batches:
        restored = ChunkCheckpoint.model_validate_json(
            batch.checkpoint.model_dump_json()
        )
        recreated = chunker(settings, counter=SparseFitCounter())
        replay, _ = await collect(recreated, reader, restored)
        assert replay == expected[restored.next_ordinal :]
        if batch.chunks:
            assert restored.next_prefix_length == 0
    uninterrupted, _ = await collect(
        chunker(settings, counter=SparseFitCounter()), reader
    )
    assert uninterrupted == expected


async def test_invalid_window_checks_every_prefix_in_bounded_batches() -> None:
    class RecordingCounter(ByteCounter):
        def __init__(self) -> None:
            super().__init__()
            self.prefix_lengths: list[int] = []

        def count_tokens(self, text: str) -> int:
            label = "description:\n"
            if text.startswith(label) and len(text) > len(label):
                self.prefix_lengths.append(len(text) - len(label))
            return super().count_tokens(text)

    settings = config(budget=1, read_size=1024)
    counter = RecordingCounter()
    reader = PatternReader({COLUMN.id: PatternSource("x", 1024)})
    splitter = chunker(settings, counter=counter)
    first = await splitter.prepare_batch(reader)
    assert not first.complete and not first.chunks
    assert first.checkpoint.character_offset == first.checkpoint.next_ordinal == 0
    assert counter.calls <= 32 * (MAX_PREFIX_PROBES + 3)
    assert reader.calls == 32
    before_calls = counter.calls
    before_reads = reader.calls
    with pytest.raises(InvalidChunkingConfig, match="any prefix"):
        await splitter.prepare_batch(reader, first.checkpoint)
    assert counter.calls - before_calls <= 32 * (MAX_PREFIX_PROBES + 3)
    assert reader.calls - before_reads <= 32
    assert counter.max_characters <= settings.read_size + len(COLUMN.name) + 2
    assert sorted(counter.prefix_lengths) == list(range(1, 1025))


async def test_nonmonotone_counts_still_emit_only_verified_inputs() -> None:
    counter = MergingCounter()
    settings = config(budget=20)
    reader = PatternReader({COLUMN.id: PatternSource("a", 1000)})
    chunks, _ = await collect(chunker(settings, counter=counter), reader)
    covered = 0
    for chunk in chunks:
        assert chunk.metadata.start <= covered < chunk.metadata.end
        assert chunk.metadata.token_count == counter.count_tokens(chunk.text) <= 20
        covered = chunk.metadata.end
    assert covered == 1000


async def test_merged_label_can_fit_even_when_standalone_label_exceeds_budget() -> None:
    counter = MergingCounter()
    # The 13-character label plus one character counts as one token in this contract.
    reader = PatternReader({COLUMN.id: PatternSource("a")})
    batch = await chunker(config(budget=1), counter=counter).prepare_batch(reader)
    assert batch.complete and batch.chunks[0].metadata.token_count == 1


@pytest.mark.parametrize(
    "field", ["revision", "generation", "config_version", "document_id", "workspace_id"]
)
async def test_reject_checkpoint_from_another_build(field: str) -> None:
    cursor = chunker().initial_checkpoint()
    value = UUID(int=999) if field.endswith("_id") else 2
    changed_identity = IDENTITY.model_copy(update={field: value})
    reader = PatternReader({COLUMN.id: PatternSource("abc")})
    with pytest.raises(InvalidCheckpoint):
        await chunker(identity=changed_identity).prepare_batch(reader, cursor)
    assert reader.calls == 0


async def test_reject_changed_config_labels_and_selection() -> None:
    cursor = chunker().initial_checkpoint()
    variants = [
        chunker(config(budget=63)),
        chunker(config(read_size=64)),
        chunker(columns=(replace(COLUMN, name="renamed"),)),
        chunker(columns=()),
    ]
    for splitter in variants:
        with pytest.raises(InvalidCheckpoint):
            await splitter.prepare_batch(PatternReader({}), cursor)


async def test_invalid_checkpoint_serialization_and_ranges() -> None:
    cursor = chunker().initial_checkpoint()
    for payload in [
        cursor.model_dump_json().replace('"version":"v1"', '"version":"v2"'),
        cursor.model_dump_json().replace(
            '"character_offset":0', '"character_offset":-1'
        ),
        cursor.model_dump_json().replace('"overlap_start":0', '"overlap_start":1'),
    ]:
        with pytest.raises(ValidationError):
            ChunkCheckpoint.model_validate_json(payload)
    for changes in [
        {"column_index": 2},
        {"character_offset": 65},
        {"column_index": 1, "character_offset": 1},
        {"next_prefix_length": 128},
        {"column_index": 1, "next_prefix_length": 2},
    ]:
        invalid = ChunkCheckpoint.model_validate(cursor.model_dump() | changes)
        with pytest.raises(InvalidCheckpoint):
            await chunker().prepare_batch(PatternReader({}), invalid)


@pytest.mark.parametrize("field", ["input_hash", "token_count", "end", "column_name"])
async def test_reconstruction_rejects_modified_metadata(field: str) -> None:
    reader = PatternReader({COLUMN.id: PatternSource("a passage")})
    splitter = chunker()
    batch = await splitter.prepare_batch(reader)
    metadata = batch.chunks[0].metadata
    changes = {
        "input_hash": "0" * 64,
        "token_count": 0,
        "end": 1,
        "column_name": "changed",
    }
    with pytest.raises(ChunkInputMismatch):
        await splitter.reconstruct_input(
            reader, replace(metadata, **{field: changes[field]})
        )


async def test_reconstruction_rejects_changed_source() -> None:
    reader = PatternReader({COLUMN.id: PatternSource("original text")})
    splitter = chunker()
    batch = await splitter.prepare_batch(reader)
    reader.sources[COLUMN.id] = PatternSource("replaced text")
    with pytest.raises(ChunkInputMismatch):
        await splitter.reconstruct_input(reader, batch.chunks[0].metadata)


async def test_reconstruction_from_persisted_reference_without_token_count() -> None:
    reader = PatternReader({COLUMN.id: PatternSource("original text")})
    splitter = chunker()
    batch = await splitter.prepare_batch(reader)
    metadata = batch.chunks[0].metadata
    reference = ChunkReference(
        identity=metadata.identity,
        config_hash=metadata.config_hash,
        column_id=metadata.column_id,
        column_name=metadata.column_name,
        ordinal=metadata.ordinal,
        start=metadata.start,
        end=metadata.end,
        input_hash=metadata.input_hash,
    )
    assert await splitter.reconstruct_input(reader, reference) == batch.chunks[0].text


@pytest.mark.parametrize(
    "source",
    [
        SourceSlice("short", False),
        SourceSlice("x" * 129, False),
        SourceSlice("\ud800", True),
    ],
)
async def test_bad_reader_contract_is_rejected(source: SourceSlice) -> None:
    class BrokenReader:
        async def read_slice(
            self, identity: ChunkingIdentity, column_id: UUID, start: int, limit: int
        ) -> SourceSlice:
            return source

    with pytest.raises(InvalidSourceSlice):
        await chunker().prepare_batch(BrokenReader())


async def test_source_reader_failure_does_not_advance_saved_checkpoint() -> None:
    reader = PatternReader({COLUMN.id: PatternSource("x", 1000)})
    splitter = chunker()
    first = await splitter.prepare_batch(reader, max_chunks=1)
    saved = first.checkpoint.model_dump_json()
    reader.identity = IDENTITY.model_copy(update={"revision": 2})
    with pytest.raises(AssertionError, match="source revision"):
        await splitter.prepare_batch(reader, first.checkpoint)
    assert first.checkpoint.model_dump_json() == saved
    reader.identity = IDENTITY
    resumed, _ = await collect(splitter, reader, first.checkpoint)
    expected, _ = await collect(splitter, reader)
    assert list(first.chunks) + resumed == expected


async def test_interruption_mid_batch_replays_without_losing_prepared_chunks() -> None:
    class InterruptedReader(PatternReader):
        fail_on_call: int | None = 3

        async def read_slice(
            self, identity: ChunkingIdentity, column_id: UUID, start: int, limit: int
        ) -> SourceSlice:
            if self.calls + 1 == self.fail_on_call:
                raise OSError("Synthetic interrupted read")
            return await super().read_slice(identity, column_id, start, limit)

    source = PatternSource("a long source with repeated passages. ", 100)
    reader = InterruptedReader({COLUMN.id: source})
    splitter = chunker()
    saved = splitter.initial_checkpoint()
    with pytest.raises(OSError, match="Synthetic interrupted read"):
        await splitter.prepare_batch(reader, saved)
    assert saved == splitter.initial_checkpoint()
    reader.fail_on_call = None
    replay = await splitter.prepare_batch(reader, saved)
    expected = await chunker().prepare_batch(PatternReader({COLUMN.id: source}), saved)
    assert replay == expected


async def test_highly_compressed_text_still_respects_character_bounds() -> None:
    class CompressedCounter(ByteCounter):
        def count_tokens(self, text: str) -> int:
            super().count_tokens(text)
            return 1

    source = PatternSource("x", 10000)
    counter = CompressedCounter()
    reader = PatternReader({COLUMN.id: source})
    chunks, _ = await collect(chunker(counter=counter), reader)
    covered = 0
    for chunk in chunks:
        assert chunk.metadata.start <= covered < chunk.metadata.end
        assert chunk.metadata.end - chunk.metadata.start <= 128
        covered = chunk.metadata.end
    assert covered == source.length
    assert reader.max_limit == 128
    assert counter.max_characters <= 128 + len(COLUMN.name) + 2


async def test_character_window_prefers_boundaries_but_final_passage_stays_whole() -> (
    None
):
    settings = config(budget=800)
    text = "a" * 70 + "\n\n" + "b" * 200
    reader = PatternReader({COLUMN.id: PatternSource(text)})
    first = await chunker(settings).prepare_batch(reader, max_chunks=1)
    assert first.chunks[0].metadata.end == 72
    final_text = "a" * 70 + "\n\n" + "b" * 10
    reader = PatternReader({COLUMN.id: PatternSource(final_text)})
    final = await chunker(settings).prepare_batch(reader)
    assert final.complete and len(final.chunks) == 1
    assert final.chunks[0].metadata.end == len(final_text)


async def test_invalid_configuration_and_batch_limits() -> None:
    for columns in [(COLUMN, COLUMN), (replace(COLUMN, name="x" * 257),)]:
        with pytest.raises(InvalidChunkingConfig):
            chunker(columns=columns)
    with pytest.raises(InvalidChunkingConfig):
        chunker(config().model_copy(update={"tokenizer": "different-tokenizer"}))
    for value in (0, 33):
        with pytest.raises(InvalidChunkingConfig):
            await chunker().prepare_batch(PatternReader({}), max_chunks=value)
        with pytest.raises(InvalidChunkingConfig):
            await chunker().prepare_batch(PatternReader({}), max_windows=value)


@pytest.mark.parametrize("resume", [False, True])
async def test_tokenization_does_not_block_the_source_event_loop(resume: bool):
    loop_thread = threading.get_ident()
    loop = asyncio.get_running_loop()
    entered = asyncio.Event()
    release = threading.Event()

    class BlockingCounter(ByteCounter):
        armed = False

        def count_tokens(self, text: str) -> int:
            if self.armed:
                assert threading.get_ident() != loop_thread
                loop.call_soon_threadsafe(entered.set)
                assert release.wait(2), "event loop could not release tokenization"
            return super().count_tokens(text)

    class LoopReader(PatternReader):
        async def read_slice(
            self, identity: ChunkingIdentity, column_id: UUID, start: int, limit: int
        ) -> SourceSlice:
            assert threading.get_ident() == loop_thread
            return await super().read_slice(identity, column_id, start, limit)

    counter = BlockingCounter()
    splitter = chunker(counter=counter)
    reader = LoopReader({COLUMN.id: PatternSource("synthetic text. ", 20)})
    if resume:
        prepared = await splitter.prepare_batch(reader, max_chunks=1)
        operation = splitter.reconstruct_input(reader, prepared.chunks[0].metadata)
    else:
        operation = splitter.prepare_batch(reader, max_chunks=1)
    counter.armed = True
    task = asyncio.create_task(operation)
    try:
        await asyncio.wait_for(entered.wait(), 1)
        # Reaching here proves the owning loop runs while tokenization waits.
        release.set()
        await asyncio.wait_for(task, 2)
    finally:
        release.set()
        await task

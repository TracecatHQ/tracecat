"""Deterministic, resumable text chunking using bounded source reads.

No source text is retained between calls. A step handles one character window;
both output chunks and source windows are bounded per batch, including when
columns are blank. Counted binary search chooses a fitting prefix/suffix, not a
guaranteed maximal one: tokenizer counts are not assumed to be monotone. When
even the minimum prefix is too large, a bounded, resumable scan finds a fitting
starting point or establishes that no prefix of the current window can fit.
"""

import hashlib
import re
from collections.abc import Sequence

import orjson

from tracecat.search.chunking_types import (
    ChunkBatch,
    ChunkCheckpoint,
    ChunkingConfig,
    ChunkingIdentity,
    ChunkInputMismatch,
    ChunkMetadata,
    ChunkReference,
    InvalidCheckpoint,
    InvalidChunkingConfig,
    InvalidSourceSlice,
    PrefixSearchPending,
    PreparedChunk,
    SourceReader,
    SourceSlice,
    TextColumn,
    TokenCounter,
)

_PARAGRAPH_END = re.compile(r"\r?\n[ \t]*\r?\n")
_SENTENCE_END = re.compile(r"[.!?](?=\s)")
MAX_BATCH_CHUNKS = 32
MAX_BATCH_WINDOWS = 32
MAX_PREFIX_PROBES = 16


def _hash_text(text: str) -> str:
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _has_surrogates(text: str) -> bool:
    return any(0xD800 <= ord(character) <= 0xDFFF for character in text)


class TextChunker:
    """Prepare bounded batches for a fixed document, columns and configuration.

    Construction uses column metadata only. The supplied reader owns I/O and
    revision checks; the token counter is local. Recreate this object after a
    restart and pass the persisted ChunkCheckpoint to prepare_batch.
    """

    def __init__(
        self,
        *,
        identity: ChunkingIdentity,
        columns: Sequence[TextColumn],
        config: ChunkingConfig,
        tokenizer: TokenCounter,
    ) -> None:
        if tokenizer.identity != config.tokenizer:
            raise InvalidChunkingConfig(
                "Tokenizer identity does not match configuration"
            )
        self.identity = identity
        self.config = config
        self._tokenizer = tokenizer
        self._columns = tuple(sorted(columns, key=lambda column: column.id))
        if len({column.id for column in self._columns}) != len(self._columns):
            raise InvalidChunkingConfig("Selected column IDs must be unique")
        for column in self._columns:
            if (
                not column.name
                or len(column.name) > config.max_label_characters
                or _has_surrogates(column.name)
            ):
                raise InvalidChunkingConfig(
                    "Column label is empty, too long or invalid"
                )
        # Include ordered labels: renaming a selected column changes its inputs.
        encoded = orjson.dumps(
            (config.model_dump(mode="json"), [(c.id, c.name) for c in self._columns]),
            option=orjson.OPT_SORT_KEYS,
        )
        self._config_hash = hashlib.sha256(encoded).hexdigest()

    def initial_checkpoint(self) -> ChunkCheckpoint:
        """Return progress before reading the first selected column."""
        return ChunkCheckpoint(identity=self.identity, config_hash=self._config_hash)

    async def prepare_batch(
        self,
        reader: SourceReader,
        checkpoint: ChunkCheckpoint | None = None,
        *,
        max_chunks: int = MAX_BATCH_CHUNKS,
        max_windows: int = MAX_BATCH_WINDOWS,
    ) -> ChunkBatch:
        """Prepare at most 32 chunks/windows, then return resumable progress.

        A batch can be incomplete with no chunks after scanning blank text or
        searching later prefixes under a non-monotone token counter.
        Limits change yield points, never chunk boundaries. Persist returned
        manifests and checkpoint atomically. Only complete batches report the
        final expected chunk count; the worker decides when to publish a row.
        """
        if not 1 <= max_chunks <= MAX_BATCH_CHUNKS:
            raise InvalidChunkingConfig("Batch chunk limit must be between 1 and 32")
        if not 1 <= max_windows <= MAX_BATCH_WINDOWS:
            raise InvalidChunkingConfig("Batch window limit must be between 1 and 32")
        cursor = checkpoint if checkpoint is not None else self.initial_checkpoint()
        self._validate_checkpoint(cursor)
        chunks: list[PreparedChunk] = []
        for _ in range(max_windows):
            if cursor.column_index == len(self._columns) or len(chunks) == max_chunks:
                break
            chunk, cursor = await self._prepare_window(reader, cursor)
            if chunk is not None:
                chunks.append(chunk)
        complete = cursor.column_index == len(self._columns)
        return ChunkBatch(
            chunks=tuple(chunks),
            checkpoint=cursor,
            complete=complete,
            expected_chunk_count=cursor.next_ordinal if complete else None,
        )

    async def reconstruct_input(
        self, reader: SourceReader, metadata: ChunkReference
    ) -> str:
        """Reread one bounded original range and verify the exact labeled input."""
        if (
            metadata.identity != self.identity
            or metadata.config_hash != self._config_hash
            or metadata.ordinal < 0
            or metadata.start < 0
            or not 0 < metadata.end - metadata.start <= self.config.read_size
        ):
            raise ChunkInputMismatch(
                "Chunk identity, configuration or range is invalid"
            )
        column = next((c for c in self._columns if c.id == metadata.column_id), None)
        if column is None or column.name != metadata.column_name:
            raise ChunkInputMismatch("Chunk column does not match selected columns")
        length = metadata.end - metadata.start
        source = await self._read(reader, column, metadata.start, length)
        if len(source.text) != length:
            raise ChunkInputMismatch("Source range is shorter than the saved chunk")
        text = self._label(column) + source.text
        tokens = self._count(text)
        if (
            _hash_text(text) != metadata.input_hash
            or (isinstance(metadata, ChunkMetadata) and tokens != metadata.token_count)
            or tokens > self.config.token_budget
        ):
            raise ChunkInputMismatch(
                "Reconstructed input does not match the saved chunk"
            )
        return text

    def _validate_checkpoint(self, cursor: ChunkCheckpoint) -> None:
        if (
            cursor.identity != self.identity
            or cursor.config_hash != self._config_hash
            or cursor.column_index > len(self._columns)
            or cursor.next_prefix_length >= self.config.read_size
            or cursor.character_offset - cursor.overlap_start
            > self.config.read_size // 2
            or (
                cursor.column_index == len(self._columns)
                and (
                    cursor.character_offset != 0
                    or cursor.overlap_start != 0
                    or cursor.next_prefix_length != 0
                )
            )
        ):
            raise InvalidCheckpoint("Checkpoint does not match this document build")

    async def _read(
        self, reader: SourceReader, column: TextColumn, start: int, limit: int
    ) -> SourceSlice:
        source = await reader.read_slice(self.identity, column.id, start, limit)
        if (
            len(source.text) > limit
            or (len(source.text) < limit and not source.end_of_column)
            or _has_surrogates(source.text)
        ):
            raise InvalidSourceSlice(
                "Reader returned an invalid bounded character slice"
            )
        return source

    def _count(self, text: str) -> int:
        tokens = self._tokenizer.count_tokens(text)
        if type(tokens) is not int or tokens < 0:
            raise InvalidChunkingConfig("Tokenizer returned an invalid token count")
        return tokens

    @staticmethod
    def _label(column: TextColumn) -> str:
        return f"{column.name}:\n"

    async def _prepare_window(
        self, reader: SourceReader, cursor: ChunkCheckpoint
    ) -> tuple[PreparedChunk | None, ChunkCheckpoint]:
        column = self._columns[cursor.column_index]
        start = cursor.overlap_start
        source = await self._read(reader, column, start, self.config.read_size)
        covered = cursor.character_offset - start
        if len(source.text) < covered:
            raise InvalidSourceSlice(
                "Source ended before the checkpoint's covered range"
            )
        # Skip only newly read whitespace; overlap has already been covered.
        if not source.text[covered:].strip():
            end = start + len(source.text)
            return None, self._advance(
                cursor, end, end, source.end_of_column, emitted=False
            )

        label = self._label(column)
        label_tokens = self._count(label)
        passage = source.text
        minimum = covered + 1
        fit = self._fitting_prefix(label, passage, minimum, cursor.next_prefix_length)
        if isinstance(fit, PrefixSearchPending):
            return None, cursor.model_copy(
                update={"next_prefix_length": fit.next_length}
            )
        if fit is None:
            if covered:
                # Only drop overlap after proving no advancing prefix fits it.
                # The next step reads from the covered end and starts a fresh
                # search; both transitions remain safe to checkpoint and replay.
                return None, cursor.model_copy(
                    update={
                        "overlap_start": cursor.character_offset,
                        "next_prefix_length": 0,
                    }
                )
            raise InvalidChunkingConfig(
                "Input budget cannot fit any prefix of the source window"
            )
        length = fit
        if not (source.end_of_column and length == len(passage)):
            length = self._preferred_boundary(label, passage, length, minimum)
        passage = passage[:length]
        end = start + length
        finished = source.end_of_column and end == cursor.overlap_start + len(
            source.text
        )
        # A fitting prefix can itself be blank even when later text is nonblank.
        if not passage.strip():
            return None, self._advance(cursor, end, end, finished, emitted=False)

        text = label + passage
        chunk = PreparedChunk(
            text=text,
            metadata=ChunkMetadata(
                identity=self.identity,
                config_hash=self._config_hash,
                column_id=column.id,
                column_name=column.name,
                ordinal=cursor.next_ordinal,
                start=start,
                end=end,
                token_count=self._count(text),
                input_hash=_hash_text(text),
            ),
        )
        overlap = 0 if finished else self._overlap_length(passage, label_tokens)
        return chunk, self._advance(cursor, end, end - overlap, finished, emitted=True)

    def _fitting_prefix(
        self, label: str, passage: str, minimum: int, next_length: int
    ) -> int | PrefixSearchPending | None:
        """Find a fit, yield its search position, or exhaust the current window."""
        budget = self.config.token_budget
        if next_length == 0:
            if self._count(label + passage) <= budget:
                return len(passage)
            if self._count(label + passage[:minimum]) <= budget:
                return self._extend_fitting_prefix(label, passage, minimum)
            next_length = minimum + 1
        elif not minimum < next_length < len(passage):
            raise InvalidCheckpoint(
                "Pending prefix search is outside its source window"
            )

        # Full input and shorter prefixes have already failed. A non-monotone
        # counter can fit at any remaining length, so test each before rejecting.
        # Yield between small groups of probes instead of scanning a whole window.
        stop = min(next_length + MAX_PREFIX_PROBES, len(passage))
        for length in range(next_length, stop):
            if self._count(label + passage[:length]) <= budget:
                return self._extend_fitting_prefix(label, passage, length)
        if stop < len(passage):
            return PrefixSearchPending(next_length=stop)
        return None

    def _extend_fitting_prefix(self, label: str, passage: str, minimum: int) -> int:
        # Keep an actually counted fitting prefix, regardless of non-monotone counts.
        low, high = minimum, len(passage)
        while low + 1 < high:
            middle = (low + high) // 2
            if self._count(label + passage[:middle]) <= self.config.token_budget:
                low = middle
            else:
                high = middle
        return low

    def _preferred_boundary(
        self, label: str, passage: str, length: int, minimum: int
    ) -> int:
        lower = max(minimum, length // 2)
        for pattern in (_PARAGRAPH_END, _SENTENCE_END):
            candidate = None
            for match in pattern.finditer(passage, lower, length):
                candidate = match.end()
            if (
                candidate is not None
                and self._count(label + passage[:candidate]) <= self.config.token_budget
            ):
                return candidate
        return length

    def _overlap_length(self, passage: str, label_tokens: int) -> int:
        budget = min(
            self.config.overlap_tokens,
            self.config.token_budget // 2,
            self.config.token_budget - label_tokens - 1,
        )
        maximum = min(len(passage) - 1, self.config.read_size // 2)
        if budget <= 0 or maximum <= 0:
            return 0
        if self._count(passage[-maximum:]) <= budget:
            return maximum
        # An empty overlap is always safe, even if one character exceeds budget.
        low, high = 0, maximum
        while low + 1 < high:
            middle = (low + high) // 2
            if self._count(passage[-middle:]) <= budget:
                low = middle
            else:
                high = middle
        return low

    def _advance(
        self,
        cursor: ChunkCheckpoint,
        end: int,
        overlap_start: int,
        finished: bool,
        *,
        emitted: bool,
    ) -> ChunkCheckpoint:
        return ChunkCheckpoint(
            identity=self.identity,
            config_hash=self._config_hash,
            column_index=cursor.column_index + int(finished),
            character_offset=0 if finished else end,
            overlap_start=0 if finished else overlap_start,
            next_ordinal=cursor.next_ordinal + int(emitted),
        )

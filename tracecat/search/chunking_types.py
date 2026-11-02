"""Local contracts for bounded text preparation, independent of search storage."""

from dataclasses import dataclass
from typing import Literal, Protocol
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, model_validator


class ChunkingError(ValueError):
    """Base for safe errors that never include source text."""


class InvalidChunkingConfig(ChunkingError):
    """The configured tokenizer, label or budget cannot prepare source text."""


class InvalidCheckpoint(ChunkingError):
    """A checkpoint does not belong to this document/configuration."""


class InvalidSourceSlice(ChunkingError):
    """The source reader broke its bounded, revision-consistent read contract."""


class ChunkInputMismatch(ChunkingError):
    """A saved chunk cannot be reconstructed from the supplied source."""


class ChunkingConfig(BaseModel):
    """Pinned settings; changing any field requires starting a new build.

    The version/normalization/label names match the storage settings contract.
    Character limits bound memory independently of tokenizer compression.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    version: Literal["v1"] = Field(default="v1")
    tokenizer: str = Field(min_length=1, max_length=256)
    input_tokens: int = Field(default=800, gt=0)
    overlap_tokens: int = Field(default=128, ge=0)
    provider_input_tokens: int = Field(gt=0)
    normalization: Literal["none"] = Field(default="none")
    label_format: Literal["column_name_newline_v1"] = Field(
        default="column_name_newline_v1"
    )
    read_size: int = Field(default=4096, ge=2, le=65536)
    max_label_characters: int = Field(default=256, ge=1, le=4096)

    @property
    def token_budget(self) -> int:
        """Total permitted tokens, including the column label."""
        return min(self.input_tokens, self.provider_input_tokens)


class ChunkingIdentity(BaseModel):
    """Caller-supplied identity of one immutable document build.

    Document ID is the search document ID, not necessarily the source row ID.
    A new worker lease does not change this identity; a new revision does.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    organization_id: UUID
    workspace_id: UUID
    collection_id: UUID
    document_id: UUID
    generation: int = Field(ge=0)
    config_version: int = Field(ge=0)
    revision: int = Field(ge=0)


@dataclass(frozen=True, slots=True)
class TextColumn:
    """Selected TEXT-column metadata; validation of the SQL type is upstream."""

    id: UUID
    name: str


@dataclass(frozen=True, slots=True)
class SourceSlice:
    """An exact requested slice, or a shorter slice at the column's end.

    Null columns are represented by an empty slice with end_of_column=True.
    Short non-final reads are invalid; a reader must assemble those internally.
    """

    text: str
    end_of_column: bool


class SourceReader(Protocol):
    """Read zero-based Unicode code-point offsets in unchanged source text.

    Check the supplied build/revision in the same consistent read as the text;
    raise on a stale revision or missing source. Do not return mixed revisions.
    Return exactly ``limit`` characters unless the column ends sooner. A read
    at the column's end returns an empty final slice. Never load a whole column
    to implement this method. PostgreSQL adapters translate start to one-based
    substring positions and must preserve newlines and Unicode unchanged.
    """

    async def read_slice(
        self,
        identity: ChunkingIdentity,
        column_id: UUID,
        start: int,
        limit: int,
    ) -> SourceSlice:
        """Read at most limit characters from the requested immutable revision."""
        ...


class TokenCounter(Protocol):
    """Deterministic local counting for a fixed tokenizer and its options.

    Count the exact provider input, including any required special tokens.
    Prefix counts need not be monotone; every emitted candidate is checked.
    Implementations must handle arbitrary valid Unicode as ordinary text and
    must not call a provider or retain inputs. Include version/options in identity.
    """

    @property
    def identity(self) -> str:
        """Stable tokenizer identity recorded in ChunkingConfig."""
        ...

    def count_tokens(self, text: str) -> int:
        """Return a nonnegative token count for a bounded string."""
        ...


class ChunkCheckpoint(BaseModel):
    """Versioned, text-free progress suitable for durable JSON storage.

    character_offset is the end already covered, overlap_start is where the
    next bounded read begins. Their difference is at most half a read window.
    next_prefix_length resumes a bounded fallback search in that same window;
    zero means no search is pending. No candidate source text is persisted.
    End-of-document is column_index == number of selected columns.
    """

    model_config = ConfigDict(frozen=True, extra="forbid", strict=True)

    version: Literal["v1"] = Field(default="v1")
    identity: ChunkingIdentity
    config_hash: str = Field(pattern=r"^[a-f0-9]{64}$")
    column_index: int = Field(default=0, ge=0)
    character_offset: int = Field(default=0, ge=0)
    overlap_start: int = Field(default=0, ge=0)
    next_ordinal: int = Field(default=0, ge=0)
    next_prefix_length: int = Field(default=0, ge=0)

    @model_validator(mode="after")
    def validate_offsets(self) -> "ChunkCheckpoint":
        if self.overlap_start > self.character_offset:
            raise InvalidCheckpoint("Overlap starts after the covered source range")
        return self


@dataclass(frozen=True, slots=True)
class PrefixSearchPending:
    """The next untested prefix after exhausting one step's counting budget."""

    next_length: int


@dataclass(frozen=True, slots=True)
class ChunkReference:
    """Stored manifest fields bound to the caller's build and configuration.

    Storage does not need to persist a token count: reconstruction counts the
    hash-verified input again under the pinned tokenizer.
    """

    identity: ChunkingIdentity
    config_hash: str
    column_id: UUID
    column_name: str
    ordinal: int
    start: int
    end: int
    input_hash: str


@dataclass(frozen=True, slots=True)
class ChunkMetadata(ChunkReference):
    """A prepared reference with the exact input token count."""

    token_count: int


@dataclass(frozen=True, slots=True)
class PreparedChunk:
    """Bounded labeled input; keep text out of workflow history and manifests."""

    metadata: ChunkMetadata
    text: str


@dataclass(frozen=True, slots=True)
class ChunkBatch:
    """Persist chunks and checkpoint together; completion is not publication."""

    chunks: tuple[PreparedChunk, ...]
    checkpoint: ChunkCheckpoint
    complete: bool
    expected_chunk_count: int | None

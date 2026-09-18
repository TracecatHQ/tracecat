"""Shared semantic indexing contracts; no provider or table implementation."""

import uuid
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field, model_validator

from tracecat.search.chunking_types import ChunkCheckpoint


class SearchState(StrEnum):
    """Workspace availability states controlling search and indexing."""

    DISABLED = "disabled"
    ACTIVE = "active"
    PAUSED = "paused"
    REINDEX_REQUIRED = "reindex_required"


class DocumentState(StrEnum):
    """Lifecycle states of a row document within an index generation."""

    PENDING = "pending"
    BUILDING = "building"
    READY = "ready"
    EMPTY = "empty"
    FAILED = "failed"
    DELETED = "deleted"


class SearchErrorCode(StrEnum):
    """Stable error codes safe to expose without source text or credentials."""

    NOT_FOUND = "NOT_FOUND"
    INDEX_NOT_READY = "INDEX_NOT_READY"
    STALE_CLAIM = "STALE_CLAIM"
    MANIFEST_CONFLICT = "MANIFEST_CONFLICT"
    INVALID_VECTOR = "INVALID_VECTOR"
    CONFIGURATION_CHANGED = "CONFIGURATION_CHANGED"
    PROVIDER_UNAVAILABLE = "PROVIDER_UNAVAILABLE"
    INVALID_CURSOR = "INVALID_CURSOR"


class SearchError(Exception):
    """Safe domain error. Never embed provider responses or source text."""

    def __init__(self, code: SearchErrorCode):
        self.code = code
        super().__init__(code.value)


@dataclass(frozen=True, slots=True)
class SearchScope:
    """Trusted organization and workspace identifiers for storage operations."""

    organization_id: uuid.UUID
    workspace_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class BuildClaim:
    """Identity and fencing token required for worker writes.

    Attributes:
        collection_id: Collection whose document is being built.
        document_id: Row document assigned to the worker.
        generation: Collection generation at claim time.
        config_version: Immutable embedding configuration used by this build.
        revision: Desired source revision being processed.
        fence: Monotonically increasing token rejecting superseded workers."""

    collection_id: uuid.UUID
    document_id: uuid.UUID
    generation: int
    config_version: int
    revision: int
    fence: int


class ChunkerSettings(BaseModel):
    """Immutable chunking settings defining a collection generation.

    Attributes:
        version: Chunker implementation version.
        tokenizer: Tokenizer used to measure embedding inputs.
        input_tokens: Target token budget per chunk.
        overlap_tokens: Tokens repeated between adjacent chunks.
        normalization: Text normalization policy.
        label_format: Format used to include a column label in embedding input."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    version: str = Field(default="v1")
    tokenizer: str
    input_tokens: int = Field(default=800, gt=0)
    overlap_tokens: int = Field(default=128, ge=0)
    normalization: str = Field(default="none")
    label_format: str = Field(default="column_name_newline_v1")
    provider_input_tokens: int | None = Field(default=None, gt=0)
    read_size: int = Field(default=4096, ge=2, le=65536)
    max_label_characters: int = Field(default=256, ge=1, le=4096)


class EnumerationCursor(BaseModel):
    """Zero-based Unicode character offsets in unmodified source text."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    column_index: int = Field(default=0, ge=0)
    character_offset: int = Field(default=0, ge=0)
    next_ordinal: int = Field(default=0, ge=0)
    chunker: ChunkCheckpoint | None = Field(default=None)

    @model_validator(mode="after")
    def check_chunker_position(self) -> "EnumerationCursor":
        if self.chunker is not None and (
            self.column_index != self.chunker.column_index
            or self.character_offset != self.chunker.character_offset
            or self.next_ordinal != self.chunker.next_ordinal
        ):
            raise ValueError("Chunker checkpoint position mismatch")
        return self


class ChunkManifest(BaseModel):
    """Chunk identity and source span persisted before embedding.

    Attributes:
        ordinal: Zero-based chunk position within the document.
        column_id: Stable source column identifier.
        column_name: Source column label at enumeration time.
        start: Inclusive Unicode character offset in unmodified source text.
        end: Exclusive Unicode character offset in unmodified source text.
        input_hash: SHA-256 hex digest of the exact embedding input."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    ordinal: int = Field(ge=0)
    column_id: uuid.UUID
    column_name: str
    start: int = Field(ge=0)
    end: int = Field(gt=0)
    input_hash: str = Field(pattern=r"^[a-f0-9]{64}$")


@dataclass(frozen=True, slots=True)
class EmbeddingResult:
    """Provider adapters return vectors bound to the exact request identity."""

    ordinal: int
    input_hash: str
    config_version: int
    vector: tuple[float, ...]


@dataclass(frozen=True, slots=True)
class EmbeddingInput:
    """One provider input bound to its chunk ordinal and input hash."""

    ordinal: int
    input_hash: str
    text: str


@dataclass(frozen=True, slots=True)
class EmbeddingRequest:
    """Provider batch scoped to one workspace and immutable configuration.

    Attributes:
        scope: Trusted tenant identity for credential and configuration lookup.
        config_version: Embedding configuration version to use.
        dimensions: Expected number of components in every returned vector.
        items: Exact chunk inputs to embed."""

    scope: SearchScope
    config_version: int
    dimensions: int
    items: tuple[EmbeddingInput, ...]

"""Shared semantic indexing contracts; no provider or table implementation."""

import uuid
from dataclasses import dataclass
from enum import StrEnum

from pydantic import BaseModel, ConfigDict, Field


class SearchState(StrEnum):
    DISABLED = "disabled"
    ACTIVE = "active"
    PAUSED = "paused"
    REINDEX_REQUIRED = "reindex_required"


class DocumentState(StrEnum):
    PENDING = "pending"
    BUILDING = "building"
    READY = "ready"
    EMPTY = "empty"
    FAILED = "failed"
    DELETED = "deleted"


class SearchErrorCode(StrEnum):
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
    organization_id: uuid.UUID
    workspace_id: uuid.UUID


@dataclass(frozen=True, slots=True)
class BuildClaim:
    collection_id: uuid.UUID
    document_id: uuid.UUID
    generation: int
    config_version: int
    revision: int
    fence: int


class ChunkerSettings(BaseModel):
    model_config = ConfigDict(frozen=True, extra="forbid")
    version: str = Field(default="v1")
    tokenizer: str
    input_tokens: int = Field(default=800, gt=0)
    overlap_tokens: int = Field(default=128, ge=0)
    normalization: str = Field(default="none")
    label_format: str = Field(default="column_name_newline_v1")


class EnumerationCursor(BaseModel):
    """Zero-based Unicode character offsets in unmodified source text."""

    model_config = ConfigDict(frozen=True, extra="forbid")
    column_index: int = Field(default=0, ge=0)
    character_offset: int = Field(default=0, ge=0)
    next_ordinal: int = Field(default=0, ge=0)


class ChunkManifest(BaseModel):
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
    ordinal: int
    input_hash: str
    text: str


@dataclass(frozen=True, slots=True)
class EmbeddingRequest:
    scope: SearchScope
    config_version: int
    dimensions: int
    items: tuple[EmbeddingInput, ...]

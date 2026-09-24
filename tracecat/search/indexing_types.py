"""Text-free Temporal inputs and bounded indexing summaries."""

from enum import StrEnum
from uuid import UUID

from pydantic import BaseModel, Field

from tracecat.search.embeddings.types import EmbeddingErrorCode
from tracecat.search.types import SearchErrorCode, SearchScope


class CollectionWork(BaseModel):
    organization_id: UUID
    workspace_id: UUID
    collection_id: UUID

    @property
    def scope(self) -> SearchScope:
        return SearchScope(self.organization_id, self.workspace_id)


class DispatchPage(BaseModel):
    collections: list[CollectionWork]
    next_cursor: UUID | None = None


class IndexingOutcome(StrEnum):
    """Successful or deferred work outcomes; failures use existing error enums."""

    IDLE = "idle"
    CAPACITY = "capacity"
    UNAVAILABLE = "unavailable"
    PAUSED = "paused"
    PUBLISHED = "published"
    PROGRESS = "progress"


class IndexingProgress(BaseModel):
    outcome: IndexingOutcome | SearchErrorCode | EmbeddingErrorCode
    discovered: int = Field(default=0, ge=0)
    prepared: int = Field(default=0, ge=0)
    embedded: int = Field(default=0, ge=0)
    cleaned: int = Field(default=0, ge=0)
    pending: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    queue_wait_seconds: float = Field(default=0, ge=0)
    prompt_tokens: int | None = None
    total_tokens: int | None = None

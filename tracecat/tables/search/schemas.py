"""Public table semantic-selection and progress contracts."""

from enum import StrEnum
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field, JsonValue

from tracecat.pagination import PageParams
from tracecat.search.schemas import SearchIndexStatus
from tracecat.search.types import DocumentState, SearchErrorCode


class TableSearchDisplayState(StrEnum):
    DISABLED = "disabled"
    UNAVAILABLE = "unavailable"
    INDEXING = "indexing"
    READY = "ready"
    UPDATING = "updating"
    NEEDS_ATTENTION = "needs_attention"


class TableSearchSelection(BaseModel):
    """Set one selection; generation zero denotes an absent collection."""

    column_id: UUID
    enabled: bool
    expected_generation: int = Field(ge=0)


class TableSearchRetry(BaseModel):
    """Retry a bounded explicit set of failed documents in the current generation."""

    expected_generation: int = Field(ge=1)
    document_ids: list[UUID] = Field(min_length=1, max_length=100)


class TableSearchConfiguration(BaseModel):
    """Persisted selection with truthful readiness; never includes credentials."""

    generation: int = Field(default=0, ge=0)
    selected_column_ids: list[UUID] = Field(default_factory=list)
    status: TableSearchDisplayState = Field(default=TableSearchDisplayState.DISABLED)
    index: SearchIndexStatus | None = Field(default=None)


class TableSearchErrorRead(BaseModel):
    """Safe domain failure, including a stale generation precondition."""

    code: SearchErrorCode | Literal["INVALID_SELECTION"]


class TableSearchDocumentProgress(BaseModel):
    """Bounded progress sample; chunk totals remain unknown until enumeration ends."""

    document_id: UUID
    row_id: UUID
    state: DocumentState
    revision: int
    expected_chunks: int | None
    sampled_chunks: int
    sampled_embedded: int
    chunks_capped: bool
    error_code: str | None


class TableSearchProgressParams(PageParams):
    """Generation-bound document progress with standard opaque pagination."""

    generation: int = Field(ge=1)
    limit: int = Field(default=20, ge=1, le=100)


class TableSearchProgressPage(BaseModel):
    generation: int
    items: list[TableSearchDocumentProgress]
    next_cursor: str | None = Field(default=None)
    prev_cursor: str | None = Field(default=None)
    has_more: bool = Field(default=False)
    has_previous: bool = Field(default=False)


class TableSearchErrorResponse(BaseModel):
    detail: TableSearchErrorRead


class TableSearchRequestValidationError(BaseModel):
    """Standard FastAPI request validation fields for the selection endpoint."""

    loc: list[str | int]
    msg: str
    type: str
    input: JsonValue = Field(default=None)
    ctx: dict[str, JsonValue] | None = Field(default=None)


class TableSearchSelectionErrorResponse(BaseModel):
    """Invalid column selection or malformed request parameters."""

    detail: TableSearchErrorRead | list[TableSearchRequestValidationError]

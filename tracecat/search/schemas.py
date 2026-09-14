"""Transport contracts shared by later table, worker and search implementations."""

import uuid

from pydantic import BaseModel, Field

from tracecat.search.types import SearchState


class SearchRequest(BaseModel):
    query: str = Field(min_length=1)
    limit: int = Field(default=10, ge=1, le=100)
    cursor: str | None = Field(default=None)
    allow_partial: bool = Field(default=False)


class SearchMatch(BaseModel):
    column_id: uuid.UUID
    column_name: str
    text: str = Field(max_length=1000)
    start: int = Field(ge=0)
    end: int = Field(ge=0)


class SearchResult(BaseModel):
    row_id: uuid.UUID
    score: float = Field(ge=-1, le=1, allow_inf_nan=False)
    match: SearchMatch
    indexed_revision: int = Field(gt=0)


class SearchIndexStatus(BaseModel):
    state: SearchState
    pending: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    empty: int = Field(default=0, ge=0)
    ready: int = Field(default=0, ge=0)
    backfill_complete: bool = Field(default=False)
    partial: bool = Field(default=False)


class SearchPage(BaseModel):
    items: list[SearchResult]
    next_cursor: str | None = Field(default=None)
    has_more: bool = Field(default=False)
    capped: bool = Field(default=False)
    index: SearchIndexStatus


class TableSearchActionInput(SearchRequest):
    table: str = Field(min_length=1)

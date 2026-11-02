"""Transport contracts shared by later table, worker and search implementations."""

import uuid

from pydantic import BaseModel, Field

from tracecat.search.types import SearchState


class SearchRequest(BaseModel):
    """Query text and bounded pagination options for semantic row search.

    Attributes:
        query: Text whose meaning is compared with indexed table content.
        limit: Maximum number of rows returned in a page.
        cursor: Opaque continuation token from a previous page.
        allow_partial: Whether results may come from an incomplete index."""

    query: str = Field(min_length=1, max_length=131072)
    limit: int = Field(default=10, ge=1, le=100)
    cursor: str | None = Field(default=None, max_length=256)
    allow_partial: bool = Field(default=False)


class SearchMatch(BaseModel):
    """Source excerpt explaining a row match.

    Attributes:
        column_id: Stable identifier of the matched source column.
        column_name: Column label recorded during indexing.
        text: Excerpt limited to 1,000 characters.
        start: Inclusive Unicode character offset in unmodified source text.
        end: Exclusive Unicode character offset in unmodified source text.
        shortened: Whether the excerpt omits the end of the winning chunk."""

    column_id: uuid.UUID
    column_name: str
    text: str = Field(max_length=1000)
    start: int = Field(ge=0)
    end: int = Field(ge=0)
    shortened: bool = Field(default=False)


class SearchResult(BaseModel):
    """One matching source row with its best excerpt and indexed revision.

    Attributes:
        row_id: Stable identifier of the source row.
        score: Cosine similarity, with larger values indicating closer matches.
        match: Source excerpt supporting the match.
        indexed_revision: Source revision used to produce this result."""

    row_id: uuid.UUID
    score: float = Field(ge=-1, le=1, allow_inf_nan=False)
    match: SearchMatch
    indexed_revision: int = Field(gt=0)


class SearchIndexStatus(BaseModel):
    """Index availability and document counts for a collection.

    Attributes:
        state: Effective workspace or collection search state.
        pending: Documents awaiting work for the current index configuration.
        failed: Documents that failed in the current index generation.
        empty: Current documents that contain no searchable chunks.
        ready: Current documents whose complete embeddings are published.
        backfill_complete: Whether all source rows have been enumerated.
        partial: Whether the index is unavailable or results may be incomplete."""

    state: SearchState
    pending: int = Field(default=0, ge=0)
    failed: int = Field(default=0, ge=0)
    empty: int = Field(default=0, ge=0)
    ready: int = Field(default=0, ge=0)
    backfill_complete: bool = Field(default=False)
    partial: bool = Field(default=False)


class SearchPage(BaseModel):
    """A page of row matches and the index status used to produce it.

    Attributes:
        items: Ranked row matches.
        next_cursor: Opaque token for the next page, when available.
        has_more: Whether another page can be requested.
        capped: Whether an implementation limit truncated the candidate set.
        index: Availability and progress of the searched index."""

    items: list[SearchResult]
    next_cursor: str | None = Field(default=None)
    has_more: bool = Field(default=False)
    capped: bool = Field(default=False)
    index: SearchIndexStatus


class TableSearchActionInput(SearchRequest):
    """Inputs for core.table.search, extending the shared query options.

    Attributes:
        table: Name of the table to search in the caller's workspace."""

    table: str = Field(min_length=1)

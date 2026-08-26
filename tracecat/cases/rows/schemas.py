from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Final

from pydantic import Field

from tracecat.core.schemas import Schema
from tracecat.tables.schemas import TableColumnRead, TableRowInsert

# Row IDs one batch link or unlink request may carry. This bounds a single
# request only; ``MAX_LINKED_ROWS_PER_TABLE`` in ``tracecat.cases.rows.service``
# bounds how many rows of a table a case can end up linked to.
MAX_CASE_ROW_BATCH_SIZE: Final[int] = 100


class CaseTableRowRead(Schema):
    id: uuid.UUID
    case_id: uuid.UUID
    table_id: uuid.UUID
    table_name: str | None = None
    row_id: uuid.UUID
    row_data: dict[str, Any] | None = None
    is_row_available: bool = True
    created_at: datetime
    updated_at: datetime


class CaseTableRowLinkCreate(Schema):
    table_id: uuid.UUID
    row_id: uuid.UUID


class CaseLinkedTableRead(Schema):
    """One table with at least one row linked to a case.

    ``row_count`` counts links, including links whose source row was deleted.

    ``columns`` carries the table's column definitions so a caller can render
    the linked rows without a separate table read. It is empty when the table
    itself is gone. ``is_index`` is not populated here; read the table directly
    when unique-index state matters.
    """

    table_id: uuid.UUID
    table_name: str | None = None
    row_count: int
    columns: list[TableColumnRead]


class CaseTableRowBatchLink(Schema):
    table_id: uuid.UUID
    row_ids: list[uuid.UUID] = Field(
        ..., min_length=1, max_length=MAX_CASE_ROW_BATCH_SIZE
    )


class CaseTableRowBatchLinkResponse(Schema):
    """linked_count + already_linked_count == number of distinct row IDs requested."""

    linked_count: int
    already_linked_count: int


class CaseTableRowBatchUnlink(Schema):
    table_id: uuid.UUID
    row_ids: list[uuid.UUID] = Field(
        ..., min_length=1, max_length=MAX_CASE_ROW_BATCH_SIZE
    )


class CaseTableRowBatchUnlinkResponse(Schema):
    """Row IDs with no link are silently skipped."""

    unlinked_count: int


class CaseTableRowInsertCreate(Schema):
    table_id: uuid.UUID
    row: TableRowInsert

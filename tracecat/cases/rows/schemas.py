from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import Field

from tracecat.core.schemas import Schema
from tracecat.tables.schemas import TableRowInsert


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


class CaseTableRowUnlink(Schema):
    table_id: uuid.UUID
    row_id: uuid.UUID


class CaseTableRowInsertCreate(Schema):
    table_id: uuid.UUID
    row: TableRowInsert


class CaseTableRowsHydrateOptions(Schema):
    include_row_data: bool = Field(default=True)

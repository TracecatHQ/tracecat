"""Schemas for case table rows."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field


class CaseTableRowLink(BaseModel):
    """Model for linking an existing table row to a case."""

    table_id: uuid.UUID = Field(..., description="ID of the table")
    row_id: uuid.UUID = Field(..., description="ID of the row in the table")


class CaseTableRowRead(BaseModel):
    """Model for reading a case table row link with full details."""

    id: uuid.UUID = Field(..., description="Case table row link ID")
    case_id: uuid.UUID = Field(..., description="Case ID")
    table_id: uuid.UUID = Field(..., description="Table ID")
    row_id: uuid.UUID = Field(..., description="Row ID from the dynamic table")
    table_name: str = Field(..., description="Name of the table")
    row_data: dict[str, Any] = Field(..., description="The actual row data from JSONB")
    created_at: datetime
    updated_at: datetime


"""Cursor-based pagination utilities for Tracecat."""

import base64
import json
from datetime import datetime
from typing import Generic, TypeVar
from uuid import UUID

from pydantic import BaseModel, Field
from sqlmodel.ext.asyncio.session import AsyncSession

T = TypeVar("T")


class CursorPaginationParams(BaseModel):
    """Parameters for cursor-based pagination."""

    limit: int = Field(default=20, ge=1, le=100, description="Maximum items per page")
    cursor: str | None = Field(default=None, description="Cursor for pagination")
    reverse: bool = Field(default=False, description="Reverse pagination direction")


class CursorPaginatedResponse(BaseModel, Generic[T]):
    """Response format for cursor-based pagination."""

    items: list[T]
    next_cursor: str | None = Field(default=None, description="Cursor for next page")
    prev_cursor: str | None = Field(
        default=None, description="Cursor for previous page"
    )
    has_more: bool = Field(default=False, description="Whether more items exist")
    has_previous: bool = Field(
        default=False, description="Whether previous items exist"
    )


class CursorData(BaseModel):
    """Internal structure for cursor data."""

    created_at: datetime
    id: str


class BaseCursorPaginator:
    """Base class for cursor-based pagination."""

    def __init__(self, session: AsyncSession):
        self.session = session

    @staticmethod
    def encode_cursor(created_at: datetime, id: UUID | str) -> str:
        """Encode a cursor from timestamp and ID."""
        cursor_data = CursorData(created_at=created_at, id=str(id))
        json_str = cursor_data.model_dump_json()
        return base64.urlsafe_b64encode(json_str.encode()).decode()

    @staticmethod
    def decode_cursor(cursor: str) -> CursorData:
        """Decode a cursor to timestamp and ID."""
        try:
            json_str = base64.urlsafe_b64decode(cursor.encode()).decode()
            data = json.loads(json_str)
            return CursorData.model_validate(data)
        except Exception as e:
            raise ValueError(f"Invalid cursor format: {e}") from e

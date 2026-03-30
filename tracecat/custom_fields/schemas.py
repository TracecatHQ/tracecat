from __future__ import annotations

from tracecat.tables.schemas import TableColumnCreate, TableColumnUpdate


class CustomFieldCreate(TableColumnCreate):
    """Create a new custom field."""


class CustomFieldUpdate(TableColumnUpdate):
    """Update a custom field."""


__all__ = [
    "CustomFieldCreate",
    "CustomFieldUpdate",
]

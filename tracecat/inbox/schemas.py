"""Inbox API schemas."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, Field

from tracecat.inbox.types import InboxItemStatus, InboxItemType


class WorkflowSummary(BaseModel):
    """Summary of a workflow for inbox item context."""

    id: uuid.UUID = Field(..., description="Workflow ID")
    title: str = Field(..., description="Workflow title")
    alias: str | None = Field(default=None, description="Workflow alias")


class UserSummary(BaseModel):
    """Summary of a user for inbox item context."""

    id: uuid.UUID = Field(..., description="User ID")
    email: str = Field(..., description="User email")
    first_name: str | None = Field(default=None, description="User first name")
    last_name: str | None = Field(default=None, description="User last name")


class InboxItemRead(BaseModel):
    """Read model for inbox items."""

    id: uuid.UUID = Field(..., description="Unique inbox item ID")
    type: InboxItemType = Field(..., description="Type of inbox item")
    title: str = Field(..., description="Display title")
    preview: str = Field(..., description="Preview text")
    status: InboxItemStatus = Field(..., description="Item status")
    unread: bool = Field(..., description="Whether the item is unread")
    created_at: datetime = Field(..., description="Creation timestamp")
    updated_at: datetime = Field(..., description="Last update timestamp")
    workflow: WorkflowSummary | None = Field(
        default=None, description="Associated workflow"
    )
    created_by: UserSummary | None = Field(
        default=None,
        description="User who created the source entity (None for automation-initiated items)",
    )
    source_id: uuid.UUID = Field(..., description="ID of the source entity")
    source_type: str = Field(
        ..., description="Type of source entity (e.g., agent_session)"
    )
    metadata: dict[str, Any] | None = Field(
        default=None, description="Type-specific metadata"
    )


class InboxPendingCount(BaseModel):
    """Count of pending inbox items that require attention."""

    count: int = Field(..., ge=0, description="Number of pending inbox items")

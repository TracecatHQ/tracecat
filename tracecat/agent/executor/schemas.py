from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel, Field

from tracecat.storage.object import StoredObject


class WorkflowOrigin(BaseModel):
    """Workflow action that launched an agent turn."""

    workflow_id: uuid.UUID
    workflow_execution_id: str
    action_ref: str
    trigger_type: str | None = None


class ApprovedToolCall(BaseModel):
    """A single approved tool call to execute."""

    tool_call_id: str
    tool_name: str
    args: dict[str, Any]


class DeniedToolCall(BaseModel):
    """A single denied tool call."""

    tool_call_id: str
    tool_name: str
    reason: str


class ToolExecutionResult(BaseModel):
    """Resolved result from executing a single tool."""

    tool_call_id: str
    tool_name: str
    result: Any
    is_error: bool = False


class ToolExecutionResultHandle(BaseModel):
    """Tool result handle passed between workflow and reconciliation activity."""

    tool_call_id: str
    tool_name: str
    stored_result: StoredObject | None = None
    result: Any = Field(default=None)
    is_error: bool = False

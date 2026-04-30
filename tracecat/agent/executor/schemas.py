from __future__ import annotations

from typing import Any

import orjson
from pydantic import BaseModel, Field

from tracecat.storage.object import StoredObject


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


def serialize_tool_result_content(result: Any) -> str:
    """Serialize a tool result to the string content Claude SDK expects."""
    if isinstance(result, str):
        return result
    try:
        return orjson.dumps(result).decode("utf-8")
    except (TypeError, ValueError):
        return str(result)


class ToolExecutionResultHandle(BaseModel):
    """Tool result handle passed between workflow and reconciliation activity."""

    tool_call_id: str
    tool_name: str
    stored_result: StoredObject | None = None
    result: Any = Field(default=None)
    is_error: bool = False

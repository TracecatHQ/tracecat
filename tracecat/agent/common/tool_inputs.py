"""Shared normalization for agent harness tool inputs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

AGENT_TOOL_NAMES = frozenset({"Agent", "Task"})
AGENT_RUNTIME_CONTROL_FIELDS = frozenset({"model", "isolation"})
READ_TOOL_NAME = "Read"
READ_TOOL_OPTIONAL_FIELDS = ("offset", "limit", "pages")


def sanitize_read_tool_input(tool_input: Mapping[str, Any] | None) -> dict[str, Any]:
    """Return the effective input for a Claude Code ``Read`` tool call.

    Some providers make every schema property required, so the model fills the
    optional ``offset``/``limit``/``pages`` fields with placeholders such as
    ``""`` or ``null``. Claude Code validates ``pages`` whenever it is present
    and rejects the call, so drop placeholder values and only keep ``pages`` for
    PDF targets.
    """
    sanitized = dict(tool_input or {})
    for field in READ_TOOL_OPTIONAL_FIELDS:
        if field not in sanitized:
            continue
        value = sanitized[field]
        if value is None or (isinstance(value, str) and not value.strip()):
            sanitized.pop(field)
    pages = sanitized.get("pages")
    file_path = sanitized.get("file_path")
    if pages is not None and (
        not isinstance(file_path, str) or not file_path.lower().endswith(".pdf")
    ):
        sanitized.pop("pages")
    return sanitized


def sanitize_agent_tool_input(
    tool_name: str,
    tool_input: Mapping[str, Any] | None,
) -> dict[str, Any]:
    """Return the effective input for an Agent or Task tool call.

    Tracecat owns subagent model selection and runtime isolation, so model-proposed
    values for those fields must not affect execution or appear as effective input
    in user-facing tool-call representations.
    """
    sanitized = dict(tool_input or {})
    if tool_name not in AGENT_TOOL_NAMES:
        return sanitized

    for field in AGENT_RUNTIME_CONTROL_FIELDS:
        sanitized.pop(field, None)
    return sanitized

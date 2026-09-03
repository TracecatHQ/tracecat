"""Shared normalization for agent harness tool inputs."""

from __future__ import annotations

from collections.abc import Mapping
from typing import Any

AGENT_TOOL_NAMES = frozenset({"Agent", "Task"})
AGENT_RUNTIME_CONTROL_FIELDS = frozenset({"model", "isolation"})


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

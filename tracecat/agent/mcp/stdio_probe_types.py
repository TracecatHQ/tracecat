"""Shared types for stdio MCP connection probing."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from tracecat.auth.types import Role
from tracecat.integrations.schemas import MCPToolSummary
from tracecat.sanitization import redact_sensitive_text

MCP_STDIO_PROBE_WORKFLOW_ID_PREFIX = "mcp-stdio-probe"
MCP_STDIO_PROBE_ACTIVITY_NAME = "probe_stdio_mcp_connection_activity"
MCP_STDIO_PERSIST_ACTIVITY_NAME = "persist_stdio_mcp_connection_activity"
MCP_STDIO_PROBE_TIMEOUT_CAP = 120
MCP_STDIO_PROBE_HARD_TIMEOUT_BUFFER = 10
_MAX_STDIO_PROBE_ERROR_LENGTH = 12_000


class StdioMCPProbeWorkflowInput(BaseModel):
    """Input for a saved stdio MCP probe workflow."""

    mcp_integration_id: uuid.UUID
    role: Role
    persist_result: bool = False


class StdioMCPProbeInput(BaseModel):
    """Input for a saved stdio MCP probe activity."""

    mcp_integration_id: uuid.UUID
    role: Role


class StdioMCPPersistInput(BaseModel):
    """Input for persisting a successful stdio MCP probe result."""

    mcp_integration_id: uuid.UUID
    role: Role
    tools: list[MCPToolSummary]


class StdioMCPProbeResult(BaseModel):
    """Result from sandboxed stdio MCP probing."""

    success: bool
    tools: list[MCPToolSummary] = Field(default_factory=list)
    message: str
    error: str | None = None


def build_stdio_mcp_probe_workflow_id(
    *, workspace_id: uuid.UUID, mcp_integration_id: uuid.UUID
) -> str:
    """Return the durable Temporal workflow id for a saved stdio MCP probe."""
    return (
        f"{MCP_STDIO_PROBE_WORKFLOW_ID_PREFIX}/ws/{workspace_id}/{mcp_integration_id}"
    )


def sanitize_stdio_probe_error(
    text: str | None, *, env: dict[str, str] | None = None
) -> str:
    """Redact common sensitive values while preserving verbose diagnostics.

    Stdio stderr is arbitrary third-party text, so this is intentionally a
    best-effort privacy boundary rather than a guarantee that every possible
    identifier is recognized. URLs are sanitized first, then exact configured
    environment values and common sensitive patterns are removed.
    """
    if not text:
        return "Stdio MCP probe failed"

    sanitized = redact_sensitive_text(
        text,
        sensitive_values=(env or {}).values(),
        redact_emails=True,
    )

    summary = sanitized.strip() or "Stdio MCP probe failed"
    if summary == "McpError: Connection closed":
        summary = (
            "The MCP server process closed before verification completed. "
            "Check the server URL and credentials, then try again."
        )
    if len(summary) <= _MAX_STDIO_PROBE_ERROR_LENGTH:
        return summary

    separator = "\n… output truncated …\n"
    head_length = 2_000
    tail_length = _MAX_STDIO_PROBE_ERROR_LENGTH - head_length - len(separator)
    return f"{summary[:head_length]}{separator}{summary[-tail_length:]}"

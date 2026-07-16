"""Shared types for stdio MCP connection probing."""

from __future__ import annotations

import re
import uuid

from pydantic import BaseModel, Field

from tracecat.auth.types import Role
from tracecat.integrations.mcp_validation import sanitize_urls_in_text
from tracecat.integrations.schemas import MCPToolSummary

MCP_STDIO_PROBE_WORKFLOW_ID_PREFIX = "mcp-stdio-probe"
MCP_STDIO_PROBE_ACTIVITY_NAME = "probe_stdio_mcp_connection_activity"
MCP_STDIO_PERSIST_ACTIVITY_NAME = "persist_stdio_mcp_connection_activity"
MCP_STDIO_PROBE_TIMEOUT = 120
MCP_STDIO_PROBE_HARD_TIMEOUT_BUFFER = 10
_MAX_STDIO_PROBE_ERROR_LENGTH = 12_000
_EMAIL_PATTERN = re.compile(r"\b[A-Z0-9._%+-]+@[A-Z0-9.-]+\.[A-Z]{2,}\b", re.I)
_BEARER_TOKEN_PATTERN = re.compile(r"(?i)\b(Bearer\s+)[A-Za-z0-9._~+/=-]+")
_AUTHORIZATION_VALUE_PATTERN = re.compile(r"(?im)\b(Authorization\s*:\s*)[^\r\n]+")
_COOKIE_VALUE_PATTERN = re.compile(r"(?im)\b((?:Set-)?Cookie\s*:\s*)[^\r\n]+")
_JWT_PATTERN = re.compile(r"\beyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\b")
_SENSITIVE_VALUE_PATTERN = re.compile(
    r"(?i)(?P<prefix>['\"]?\b(?:api[ _-]?key|access[ _-]?token|refresh[ _-]?token|"
    r"id[ _-]?token|client[ _-]?secret|private[ _-]?key|secret[ _-]?key|token|"
    r"password|passwd|secret)\b['\"]?\s*[:=]\s*)"
    r"(?:"
    r'(?P<double_quoted>"(?:\\.|[^"\\\r\n])*")|'
    r"(?P<single_quoted>'(?:\\.|[^'\\\r\n])*')|"
    r"(?P<unquoted>[^\s,;}\]\"']+)"
    r")"
)


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

    sanitized = sanitize_urls_in_text(text)
    for value in (env or {}).values():
        if isinstance(value, str) and len(value) >= 4:
            sanitized = sanitized.replace(value, "[redacted]")
            sanitized_value = sanitize_urls_in_text(value)
            if sanitized_value != value:
                sanitized = sanitized.replace(sanitized_value, "[redacted]")
    sanitized = _EMAIL_PATTERN.sub("[redacted email]", sanitized)
    sanitized = _BEARER_TOKEN_PATTERN.sub(r"\1[redacted]", sanitized)
    sanitized = _AUTHORIZATION_VALUE_PATTERN.sub(r"\1[redacted]", sanitized)
    sanitized = _COOKIE_VALUE_PATTERN.sub(r"\1[redacted]", sanitized)
    sanitized = _JWT_PATTERN.sub("[redacted token]", sanitized)
    sanitized = _SENSITIVE_VALUE_PATTERN.sub(_redact_sensitive_value, sanitized)

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


def _redact_sensitive_value(match: re.Match[str]) -> str:
    """Replace a key-value secret while retaining readable delimiters."""
    if match.group("double_quoted") is not None:
        redacted = '"[redacted]"'
    elif match.group("single_quoted") is not None:
        redacted = "'[redacted]'"
    else:
        redacted = "[redacted]"
    return f"{match.group('prefix')}{redacted}"

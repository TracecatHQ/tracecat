"""Domain types for Watchtower."""

from __future__ import annotations

from enum import StrEnum


class WatchtowerAgentType(StrEnum):
    """Normalized local-agent classifications stored by Watchtower."""

    CLAUDE_CODE = "claude_code"
    CODEX = "codex"
    CURSOR = "cursor"
    WINDSURF = "windsurf"
    OPENCODE = "opencode"
    OPENCLAW = "openclaw"
    UNKNOWN = "unknown"


class WatchtowerAgentStatus(StrEnum):
    """Derived status for Watchtower agents in monitor APIs."""

    ACTIVE = "active"
    IDLE = "idle"
    BLOCKED = "blocked"


class WatchtowerToolCallStatus(StrEnum):
    """Tool call result status for Watchtower monitor APIs."""

    SUCCESS = "success"
    ERROR = "error"
    TIMEOUT = "timeout"
    REJECTED = "rejected"
    BLOCKED = "blocked"

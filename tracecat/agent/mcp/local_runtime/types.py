"""Types for sandboxed local stdio MCP discovery."""

from __future__ import annotations

from dataclasses import dataclass, field
from enum import StrEnum
from pathlib import Path
from typing import Any

from tracecat.agent.common.types import MCPStdioServerConfig


class LocalMCPDiscoveryPhase(StrEnum):
    """Phase-specific local MCP discovery failure codes."""

    CONFIG_VALIDATION = "config_validation"
    PACKAGE_FETCH_INSTALL = "package_fetch_install"
    PROCESS_SPAWN = "process_spawn"
    INITIALIZE_HANDSHAKE = "initialize_handshake"
    LIST_TOOLS = "list_tools"
    LIST_RESOURCES = "list_resources"
    LIST_PROMPTS = "list_prompts"
    TIMEOUT = "timeout"
    SHUTDOWN = "shutdown"


@dataclass(frozen=True, slots=True)
class LocalMCPDiscoveryConfig:
    """Configuration for a sandboxed local stdio discovery run."""

    organization_id: str
    server: MCPStdioServerConfig
    sandbox_cache_dir: Path
    allow_network: bool
    egress_allowlist: tuple[str, ...] = ()
    egress_denylist: tuple[str, ...] = ()
    timeout_seconds: int | None = None


@dataclass(slots=True)
class LocalMCPDiscoveryError(RuntimeError):
    """User-safe local discovery failure with phase metadata."""

    phase: LocalMCPDiscoveryPhase
    summary: str
    details: dict[str, Any] = field(default_factory=dict)

    def __post_init__(self) -> None:
        RuntimeError.__init__(self, self.summary)

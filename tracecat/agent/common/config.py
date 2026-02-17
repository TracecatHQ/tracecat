"""Sandbox configuration constants.

This module defines configuration constants for the agent sandbox by reading
directly from os.environ. It does NOT import from tracecat.config to keep
the import footprint minimal for fast sandbox cold start.
"""

from __future__ import annotations

import os
from pathlib import Path

# === Agent Sandbox Config (read directly from env) === #

TRACECAT__AGENT_SANDBOX_TIMEOUT = int(
    os.environ.get("TRACECAT__AGENT_SANDBOX_TIMEOUT", "600")
)
"""Default timeout for agent sandbox execution in seconds (10 minutes)."""

TRACECAT__AGENT_SANDBOX_MEMORY_MB = int(
    os.environ.get("TRACECAT__AGENT_SANDBOX_MEMORY_MB", "4096")
)
"""Default memory limit for agent sandbox execution in megabytes (4 GiB)."""

TRACECAT__DISABLE_NSJAIL = os.environ.get(
    "TRACECAT__DISABLE_NSJAIL", "true"
).lower() in ("true", "1")
"""Disable nsjail sandbox and use the unsafe PID executor instead."""

# === Well-known socket paths (internal to agent worker) === #

TRUSTED_MCP_SOCKET_PATH = Path("/var/run/tracecat/mcp.sock")
"""Path to the trusted MCP socket (shared across jobs)."""

CONTROL_SOCKET_NAME = "control.sock"
"""Name of the per-job control socket."""

JAILED_CONTROL_SOCKET_PATH = Path("/var/run/tracecat/control.sock")
"""Path to the control socket inside the jail."""

LLM_SOCKET_NAME = "llm.sock"
"""Name of the LLM socket for proxied LiteLLM access."""

JAILED_LLM_SOCKET_PATH = Path("/var/run/tracecat/llm.sock")
"""Path to the LLM socket inside the jail."""

# === Runtime socket overrides (primarily for direct subprocess mode) === #
#
# In NSJail mode, the orchestrator mounts per-job sockets into the jailed paths above.
# In direct subprocess mode (TRACECAT__DISABLE_NSJAIL=true), there is no mount, so the
# runtime must connect to the orchestrator's real socket paths.
TRACECAT__AGENT_CONTROL_SOCKET_PATH = Path(
    os.environ.get(
        "TRACECAT__AGENT_CONTROL_SOCKET_PATH", str(JAILED_CONTROL_SOCKET_PATH)
    )
)
"""Path to the orchestrator control socket for the runtime to connect to."""

TRACECAT__AGENT_LLM_SOCKET_PATH = Path(
    os.environ.get("TRACECAT__AGENT_LLM_SOCKET_PATH", str(JAILED_LLM_SOCKET_PATH))
)
"""Path to the orchestrator LLM socket for the runtime bridge to connect to."""

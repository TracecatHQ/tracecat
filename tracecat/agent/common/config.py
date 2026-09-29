"""Sandbox configuration constants.

This module defines configuration constants for the agent sandbox by reading
directly from os.environ. It does NOT import from tracecat.config to keep
the import footprint minimal for fast sandbox cold start.
"""

from __future__ import annotations

import os
from pathlib import Path

from tracecat.executor.types import ExecutorBackendType

# === Agent Sandbox Config (read directly from env) === #

TRACECAT__AGENT_SANDBOX_TIMEOUT = int(
    os.environ.get("TRACECAT__AGENT_SANDBOX_TIMEOUT") or 3600
)
"""Ceiling for agent execution timeouts in seconds (default one hour)."""

TRACECAT__AGENT_SANDBOX_MEMORY_MB = int(
    os.environ.get("TRACECAT__AGENT_SANDBOX_MEMORY_MB") or 4096
)
"""Aggregate cgroup memory budget for each agent sandbox in MiB (4 GiB)."""

# Leave unset to derive the address-space guard from each sandbox's memory budget.
_address_space_mb = os.environ.get(
    "TRACECAT__AGENT_SANDBOX_ADDRESS_SPACE_MB", ""
).strip()
TRACECAT__AGENT_SANDBOX_ADDRESS_SPACE_MB = (
    int(_address_space_mb) if _address_space_mb else None
)
"""Per-process address-space cap in MiB; defaults to twice the cgroup budget."""
if (
    TRACECAT__AGENT_SANDBOX_ADDRESS_SPACE_MB is not None
    and TRACECAT__AGENT_SANDBOX_ADDRESS_SPACE_MB <= 0
):
    raise ValueError("TRACECAT__AGENT_SANDBOX_ADDRESS_SPACE_MB must be positive")

TRACECAT__EXECUTOR_BACKEND = ExecutorBackendType.from_config(
    os.environ.get("TRACECAT__EXECUTOR_BACKEND")
)
"""Execution mode shared with action execution and registry sync."""

_AGENT_RUNTIME_UV_PATH_ENV_VARS = (
    ("UV_CACHE_DIR", "cache"),
    ("UV_CREDENTIALS_DIR", "credentials"),
    ("UV_PYTHON_INSTALL_DIR", "python"),
    ("UV_PYTHON_BIN_DIR", "bin"),
    ("UV_PYTHON_CACHE_DIR", "python-cache"),
    ("UV_TOOL_DIR", "tools"),
    ("UV_TOOL_BIN_DIR", "bin"),
)

AGENT_RUNTIME_PROTECTED_ENV_VARS = frozenset(
    {
        "UV_LINK_MODE",
        "TRACECAT__SANDBOX_RLIMIT_NPROC",
        *(key for key, _relative_path in _AGENT_RUNTIME_UV_PATH_ENV_VARS),
    }
)
"""Environment variables reserved for Tracecat's agent runtime isolation."""


def build_agent_runtime_uv_env(uv_state_dir: Path) -> dict[str, str]:
    """Build job-scoped environment settings for UV-managed runtime storage."""
    env = {
        key: str(uv_state_dir / relative_path)
        for key, relative_path in _AGENT_RUNTIME_UV_PATH_ENV_VARS
    }
    env["UV_LINK_MODE"] = "copy"
    return env


# === Well-known runtime paths (internal to agent worker) === #

AGENT_RUNTIME_DIR = Path("/run/tracecat")
"""Tracecat-owned runtime namespace for agent sandbox mountpoints and sockets."""

TRUSTED_MCP_SOCKET_PATH = AGENT_RUNTIME_DIR / "mcp.sock"
"""Path to the trusted MCP socket (shared across jobs)."""

TRACECAT__AGENT_MCP_SOCKET_PATH = Path(
    os.environ.get("TRACECAT__AGENT_MCP_SOCKET_PATH") or str(TRUSTED_MCP_SOCKET_PATH)
)
"""Path to the trusted MCP socket visible to the runtime shim."""

TRACECAT__AGENT_MCP_BRIDGE_PORT = int(
    os.environ.get("TRACECAT__AGENT_MCP_BRIDGE_PORT") or 4101
)
"""Loopback port for the in-sandbox trusted MCP HTTP bridge."""

CONTROL_SOCKET_NAME = "control.sock"
"""Name of the per-job control socket."""

JAILED_CONTROL_SOCKET_PATH = AGENT_RUNTIME_DIR / "control.sock"
"""Path to the control socket inside the jail.

Trust model: anything mounted into the jail can be reached by untrusted
in-jail code, so a mounted control socket provides transport isolation only,
not authentication. The production agent transport deliberately does NOT
mount this socket into the jail (control_socket_required=False in
claude_code/transport.py) — jail code only reaches the orchestrator through
the LLM and MCP socket proxies. If a future path mounts it, the orchestrator
must treat every inbound control message as originating from the untrusted
sandbox and validate it strictly (schema and semantics). Adding per-job
cryptographic authentication would be the next hardening step.
"""

LLM_SOCKET_NAME = "llm.sock"
"""Name of the LLM socket for proxied LLM gateway access."""

JAILED_LLM_SOCKET_PATH = AGENT_RUNTIME_DIR / "llm.sock"
"""Path to the LLM socket inside the jail."""

OTEL_SOCKET_NAME = "otel.sock"
"""Name of the per-job Agent OTel relay socket."""

JAILED_OTEL_SOCKET_PATH = Path("/var/run/tracecat/otel.sock")
"""Path to the Agent OTel relay socket inside the jail."""

# === Runtime socket overrides (primarily for direct subprocess mode) === #
#
# In NSJail mode, the orchestrator mounts per-job sockets into the jailed paths above.
# In direct subprocess mode (TRACECAT__EXECUTOR_BACKEND=direct), there is no mount, so the
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

TRACECAT__AGENT_OTEL_SOCKET_PATH = Path(
    os.environ.get("TRACECAT__AGENT_OTEL_SOCKET_PATH", str(JAILED_OTEL_SOCKET_PATH))
)
"""Path to the orchestrator OTel relay socket for the runtime bridge to connect to."""

# === Managed LiteLLM defaults === #

TRACECAT__LITELLM_PORT = int(os.environ.get("TRACECAT__LITELLM_PORT") or 4000)
"""Bind port for the managed LiteLLM service."""

TRACECAT__LITELLM_BASE_URL = os.environ.get(
    "TRACECAT__LITELLM_BASE_URL", f"http://127.0.0.1:{TRACECAT__LITELLM_PORT}"
)
"""Internal base URL for the managed LiteLLM service."""

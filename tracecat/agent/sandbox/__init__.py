"""Sandboxed agent runtime utilities.

This package provides protocol models and I/O utilities for running
agent runtimes in isolated (NSJail) sandboxes without database access.

The orchestrator (in a separate module) is responsible for:
- Creating Unix sockets for communication
- Starting the trusted MCP HTTP server
- Managing the runtime lifecycle

This package provides:
- spawn_jailed_runtime(): Entrypoint to spawn a jailed ClaudeAgentRuntime
- Protocol models for orchestrator-runtime communication
- Socket I/O utilities for the runtime
"""

from tracecat.agent.sandbox.config import (
    AgentResourceLimits,
    AgentSandboxConfig,
    build_agent_env_map,
    build_agent_nsjail_config,
)
from tracecat.agent.sandbox.exceptions import (
    AgentSandboxError,
    AgentSandboxExecutionError,
    AgentSandboxTimeoutError,
    AgentSandboxValidationError,
)
from tracecat.agent.sandbox.nsjail import spawn_jailed_runtime, wait_for_process
from tracecat.agent.sandbox.protocol import (
    RuntimeEventEnvelope,
    RuntimeEventEnvelopeTA,
    RuntimeInitPayload,
    RuntimeInitPayloadTA,
)
from tracecat.agent.sandbox.socket_io import (
    HEADER_SIZE,
    MessageType,
    SocketStreamWriter,
    build_message,
    read_message,
)
from tracecat.sandbox.utils import is_nsjail_available

__all__ = [
    # Config
    "AgentResourceLimits",
    "AgentSandboxConfig",
    "build_agent_env_map",
    "build_agent_nsjail_config",
    # Exceptions
    "AgentSandboxError",
    "AgentSandboxExecutionError",
    "AgentSandboxTimeoutError",
    "AgentSandboxValidationError",
    # NSJail spawning
    "is_nsjail_available",
    "spawn_jailed_runtime",
    "wait_for_process",
    # Protocol
    "RuntimeEventEnvelope",
    "RuntimeEventEnvelopeTA",
    "RuntimeInitPayload",
    "RuntimeInitPayloadTA",
    # Socket I/O
    "HEADER_SIZE",
    "MessageType",
    "SocketStreamWriter",
    "build_message",
    "read_message",
]

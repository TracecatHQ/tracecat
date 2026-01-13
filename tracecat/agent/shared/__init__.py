"""Lightweight shared module for agent sandbox communication.

This module provides types and utilities for orchestrator-runtime communication
with minimal dependencies. It is designed to be imported by the sandbox entrypoint
without pulling in heavy tracecat modules.

Key design principles:
- No imports from tracecat.config (reads os.environ directly)
- Pure dataclasses instead of Pydantic models
- orjson for serialization (no pydantic_core)
"""

from tracecat.agent.shared.adapter_base import BaseHarnessAdapter
from tracecat.agent.shared.config import (
    JAILED_CONTROL_SOCKET_PATH,
    JAILED_LLM_SOCKET_PATH,
    TRACECAT__AGENT_SANDBOX_MEMORY_MB,
    TRACECAT__AGENT_SANDBOX_TIMEOUT,
    TRACECAT__DISABLE_NSJAIL,
)
from tracecat.agent.shared.exceptions import (
    AgentSandboxError,
    AgentSandboxExecutionError,
    AgentSandboxTimeoutError,
    AgentSandboxValidationError,
)
from tracecat.agent.shared.protocol import (
    RuntimeEventEnvelope,
    RuntimeInitPayload,
)
from tracecat.agent.shared.socket_io import (
    HEADER_SIZE,
    MAX_PAYLOAD_SIZE,
    MessageType,
    SocketStreamWriter,
    build_message,
    read_message,
)
from tracecat.agent.shared.stream_types import (
    HarnessType,
    StreamEventType,
    ToolCallContent,
    UnifiedStreamEvent,
)
from tracecat.agent.shared.types import (
    MCPServerConfig,
    MCPToolDefinition,
    SandboxAgentConfig,
)

__all__ = [
    # Adapter base
    "BaseHarnessAdapter",
    # Config
    "JAILED_CONTROL_SOCKET_PATH",
    "JAILED_LLM_SOCKET_PATH",
    "TRACECAT__AGENT_SANDBOX_MEMORY_MB",
    "TRACECAT__AGENT_SANDBOX_TIMEOUT",
    "TRACECAT__DISABLE_NSJAIL",
    # Exceptions
    "AgentSandboxError",
    "AgentSandboxExecutionError",
    "AgentSandboxTimeoutError",
    "AgentSandboxValidationError",
    # Protocol
    "RuntimeEventEnvelope",
    "RuntimeInitPayload",
    # Socket I/O
    "HEADER_SIZE",
    "MAX_PAYLOAD_SIZE",
    "MessageType",
    "SocketStreamWriter",
    "build_message",
    "read_message",
    # Stream types
    "HarnessType",
    "StreamEventType",
    "ToolCallContent",
    "UnifiedStreamEvent",
    # Types
    "MCPServerConfig",
    "MCPToolDefinition",
    "SandboxAgentConfig",
]

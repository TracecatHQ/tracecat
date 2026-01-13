"""Generic CLI entry point for sandboxed agent runtimes.

This module provides the main() entry point that is invoked by nsjail.
It handles socket connection and payload parsing, then delegates to
the dynamically loaded runtime implementation.

Protocol: msg_type (1B) | length (4B) | payload (length B)

Usage:
    python -m tracecat.agent.sandbox.entrypoint
"""

from __future__ import annotations

import asyncio
import importlib
from typing import TYPE_CHECKING

import orjson

from tracecat.agent.sandbox.llm_bridge import LLMBridge
from tracecat.agent.shared.config import JAILED_CONTROL_SOCKET_PATH
from tracecat.agent.shared.protocol import RuntimeInitPayload
from tracecat.agent.shared.socket_io import (
    MessageType,
    SocketStreamWriter,
    read_message,
)
from tracecat.logger import logger

if TYPE_CHECKING:
    from tracecat.agent.runtime.base import BaseRuntime

# Registry of available runtimes (lazy imports for cold start optimization)
RUNTIME_REGISTRY: dict[str, str] = {
    "claude_code": "tracecat.agent.runtime.claude_code.runtime.ClaudeAgentRuntime",
}


def _load_runtime(runtime_type: str) -> type[BaseRuntime]:
    """Dynamically load a runtime class by type.

    Args:
        runtime_type: The runtime type key from RUNTIME_REGISTRY.

    Returns:
        The runtime class.

    Raises:
        KeyError: If runtime_type is not in the registry.
        ImportError: If the module cannot be imported.
        AttributeError: If the class doesn't exist in the module.
    """
    if runtime_type not in RUNTIME_REGISTRY:
        raise KeyError(
            f"Unknown runtime type: {runtime_type}. "
            f"Available: {list(RUNTIME_REGISTRY.keys())}"
        )

    module_path, class_name = RUNTIME_REGISTRY[runtime_type].rsplit(".", 1)
    module = importlib.import_module(module_path)
    return getattr(module, class_name)


async def run_sandboxed_runtime() -> None:
    """Entry point for sandboxed runtime execution.

    Connects to orchestrator socket at the well-known jailed path,
    receives init payload, instantiates the runtime, and runs the agent.

    In NSJail mode, also starts the LLM bridge to proxy HTTP traffic
    to the LiteLLM socket.
    """
    socket_path = JAILED_CONTROL_SOCKET_PATH
    logger.info("Starting sandboxed runtime", socket_path=str(socket_path))

    # Start LLM bridge - we're inside NSJail so network is isolated
    # The bridge binds localhost:4000 and forwards to the LLM Unix socket
    llm_bridge = LLMBridge()
    await llm_bridge.start()
    logger.info("LLM bridge started")

    # Connect to orchestrator
    reader, writer = await asyncio.open_unix_connection(socket_path)
    socket_writer = SocketStreamWriter(writer)

    try:
        # Read init payload using typed message protocol
        _, init_data = await read_message(reader, expected_type=MessageType.INIT)
        if not init_data:
            raise RuntimeError("No init payload received from orchestrator")

        # Parse with orjson + dataclass (lightweight, no Pydantic TypeAdapter)
        payload = RuntimeInitPayload.from_dict(orjson.loads(init_data))
        logger.info(
            "Received init payload",
            session_id=str(payload.session_id),
            runtime_type=payload.runtime_type,
            has_session_data=bool(payload.sdk_session_data),
        )

        # Load runtime dynamically based on payload
        RuntimeClass = _load_runtime(payload.runtime_type)
        logger.info("Creating runtime", runtime_type=payload.runtime_type)

        try:
            runtime = RuntimeClass(socket_writer)
            logger.info("Runtime created, calling run()")
        except Exception as e:
            logger.exception("FAILED to create runtime", error=str(e))
            raise

        try:
            await runtime.run(payload)
            logger.info("Runtime completed")
        except Exception as e:
            logger.exception("runtime.run() FAILED", error=str(e))
            raise

    except Exception as e:
        logger.exception("Runtime error", error=str(e))
        raise
    finally:
        # Clean up LLM bridge
        await llm_bridge.stop()


def main() -> None:
    """CLI entry point for sandboxed runtime."""
    asyncio.run(run_sandboxed_runtime())


if __name__ == "__main__":
    main()

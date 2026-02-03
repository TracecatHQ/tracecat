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

from tracecat.agent.common.config import (
    TRACECAT__AGENT_CONTROL_SOCKET_PATH,
    TRACECAT__AGENT_LLM_SOCKET_PATH,
    TRACECAT__DISABLE_NSJAIL,
)
from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.common.socket_io import (
    MessageType,
    SocketStreamWriter,
    read_message,
)
from tracecat.agent.sandbox.llm_bridge import LLMBridge
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

    LLM bridge behavior (SDK always uses localhost:4000):
    - enable_internet_access=False: Network isolated, bridge on port 4000
      proxies HTTP to gateway via Unix socket.
    - enable_internet_access=True: Shares host network, SDK connects directly
      to gateway on port 4000. No bridge needed (would cause port conflicts
      across concurrent sandboxes).
    """
    socket_path = TRACECAT__AGENT_CONTROL_SOCKET_PATH
    logger.info("Starting sandboxed runtime", socket_path=str(socket_path))

    llm_bridge: LLMBridge | None = None
    socket_writer: SocketStreamWriter | None = None

    try:
        # Connect to orchestrator first to get config
        reader, writer = await asyncio.open_unix_connection(socket_path)
        socket_writer = SocketStreamWriter(writer)
        # Read init payload using typed message protocol
        _, init_data = await read_message(reader, expected_type=MessageType.INIT)
        if not init_data:
            raise RuntimeError("No init payload received from orchestrator")

        # Parse with orjson + dataclass (lightweight, no Pydantic TypeAdapter)
        payload = RuntimeInitPayload.from_dict(orjson.loads(init_data))

        # Start LLM bridge only when:
        # - network is isolated (no internet access) AND
        # - we are running in nsjail mode (port 4000 is namespaced, so no conflicts)
        #
        # In direct subprocess mode there's no network namespace, so binding 127.0.0.1:4000
        # can collide with the host LiteLLM process (which also uses port 4000).
        if not (payload.config.enable_internet_access or TRACECAT__DISABLE_NSJAIL):
            llm_bridge = LLMBridge(socket_path=TRACECAT__AGENT_LLM_SOCKET_PATH)
            await llm_bridge.start()
            logger.info("LLM bridge started (nsjail network-isolated mode)")
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
        # Send error envelope to orchestrator so it can be streamed to frontend
        if socket_writer is not None:
            try:
                await socket_writer.send_error(str(e))
                await socket_writer.send_done()
            except Exception:
                pass  # Best effort - socket may already be closed
        raise
    finally:
        if socket_writer is not None:
            await socket_writer.close()
        # Clean up LLM bridge
        if llm_bridge is not None:
            await llm_bridge.stop()


def main() -> None:
    """CLI entry point for sandboxed runtime."""
    asyncio.run(run_sandboxed_runtime())


if __name__ == "__main__":
    main()

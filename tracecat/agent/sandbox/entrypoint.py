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
import os
from pathlib import Path
from typing import TYPE_CHECKING

import orjson

from tracecat.agent.common.config import (
    TRACECAT__AGENT_CONTROL_SOCKET_PATH,
    TRACECAT__AGENT_LLM_SOCKET_PATH,
    TRACECAT__DISABLE_NSJAIL,
)
from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.common.socket_io import SocketStreamWriter
from tracecat.agent.sandbox.llm_bridge import LLMBridge
from tracecat.logger import logger

if TYPE_CHECKING:
    from tracecat.agent.runtime.base import BaseRuntime

# Registry of available runtimes (lazy imports for cold start optimization)
RUNTIME_REGISTRY: dict[str, str] = {
    "claude_code": "tracecat.agent.runtime.claude_code.runtime.ClaudeAgentRuntime",
}
DIRECT_INIT_PAYLOAD_ENV_VAR = "TRACECAT__AGENT_INIT_PAYLOAD_PATH"
JAILED_INIT_PAYLOAD_PATH = Path("/work/init.json")


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


async def _read_init_payload(
    init_path: Path,
) -> RuntimeInitPayload:
    """Read and deserialize the runtime init payload from the mounted job dir."""

    def _read_bytes() -> bytes:
        return init_path.read_bytes()

    payload_bytes = await asyncio.to_thread(_read_bytes)
    logger.info("Read init payload file", init_path=str(init_path))
    return RuntimeInitPayload.from_dict(orjson.loads(payload_bytes))


def _resolve_init_payload_path() -> Path:
    """Resolve the runtime init payload path for the current sandbox mode."""
    if not TRACECAT__DISABLE_NSJAIL:
        return JAILED_INIT_PAYLOAD_PATH
    if init_payload_path := os.environ.get(DIRECT_INIT_PAYLOAD_ENV_VAR):
        return Path(init_payload_path)
    return Path("init.json")


async def run_sandboxed_runtime() -> None:
    """Entry point for sandboxed runtime execution.

    Reads init payload from the mounted job directory, connects to the
    orchestrator socket, instantiates the runtime, and runs the agent.

    The LLM bridge is always started to proxy SDK HTTP traffic through the
    Unix socket to the host-side LLMSocketProxy. Port allocation:
    - NSJail mode + network isolated: Fixed port 4000 (own network namespace)
    - Otherwise (direct mode, or internet-enabled nsjail sharing host network):
      Dynamic port (port=0) to avoid clashes between concurrent runs
    """
    socket_path = TRACECAT__AGENT_CONTROL_SOCKET_PATH
    logger.info("Starting sandboxed runtime", socket_path=str(socket_path))

    llm_bridge: LLMBridge | None = None
    socket_writer: SocketStreamWriter | None = None

    try:
        payload = await _read_init_payload(_resolve_init_payload_path())

        # Always start the LLM bridge — the SDK needs a localhost HTTP endpoint
        # to reach the host-side LLMSocketProxy via Unix socket.
        llm_bridge = LLMBridge(
            socket_path=TRACECAT__AGENT_LLM_SOCKET_PATH,
            port=0,
        )
        async with asyncio.TaskGroup() as tg:
            bridge_task = tg.create_task(llm_bridge.start())
            socket_task = tg.create_task(asyncio.open_unix_connection(socket_path))

        bridge_port = bridge_task.result()
        _, writer = socket_task.result()
        socket_writer = SocketStreamWriter(writer)
        os.environ["TRACECAT__LLM_BRIDGE_PORT"] = str(bridge_port)
        logger.info("LLM bridge started", port=bridge_port)
        logger.info(
            "Loaded init payload",
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

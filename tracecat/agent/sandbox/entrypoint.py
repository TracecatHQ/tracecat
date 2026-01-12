"""Generic CLI entry point for sandboxed agent runtimes.

This module provides the main() entry point that is invoked by nsjail.
It handles socket connection and payload parsing, then delegates to
the AgentRuntime implementation.

Protocol: msg_type (1B) | length (4B) | payload (length B)

Usage:
    python -m tracecat.agent.sandbox.entrypoint
"""

from __future__ import annotations

import asyncio

from tracecat.agent.sandbox.config import JAILED_CONTROL_SOCKET_PATH
from tracecat.agent.sandbox.protocol import RuntimeInitPayloadTA
from tracecat.agent.sandbox.runtime import ClaudeAgentRuntime
from tracecat.agent.sandbox.socket_io import (
    MessageType,
    SocketStreamWriter,
    read_message,
)
from tracecat.logger import logger


async def run_sandboxed_runtime() -> None:
    """Entry point for sandboxed runtime execution.

    Connects to orchestrator socket at the well-known jailed path,
    receives init payload, instantiates the runtime, and runs the agent.
    """
    socket_path = JAILED_CONTROL_SOCKET_PATH
    logger.info("Starting sandboxed runtime", socket_path=str(socket_path))

    # Connect to orchestrator
    reader, writer = await asyncio.open_unix_connection(socket_path)
    socket_writer = SocketStreamWriter(writer)

    try:
        # Read init payload using typed message protocol
        _, init_data = await read_message(reader, expected_type=MessageType.INIT)
        if not init_data:
            raise RuntimeError("No init payload received from orchestrator")

        payload = RuntimeInitPayloadTA.validate_json(init_data)
        logger.info(
            "Received init payload",
            session_id=str(payload.session_id),
            has_session_data=bool(payload.sdk_session_data),
        )

        # Instantiate and run the runtime
        logger.info("Creating ClaudeAgentRuntime")
        try:
            runtime = ClaudeAgentRuntime(socket_writer)
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


def main() -> None:
    """CLI entry point for sandboxed runtime."""
    asyncio.run(run_sandboxed_runtime())


if __name__ == "__main__":
    main()

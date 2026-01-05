"""Generic CLI entry point for sandboxed agent runtimes.

This module provides the main() entry point that is invoked by nsjail.
It handles socket connection and payload parsing, then delegates to
the AgentRuntime implementation.

Protocol: msg_type (1B) | length (4B) | payload (length B)

Usage:
    python -m tracecat.agent.sandbox.entrypoint --socket /path/to/socket
"""

from __future__ import annotations

import argparse
import asyncio

from tracecat.agent.runtime import ClaudeAgentRuntime
from tracecat.agent.sandbox.protocol import RuntimeInitPayloadTA
from tracecat.agent.sandbox.socket_io import (
    MessageType,
    SocketStreamWriter,
    read_message,
)
from tracecat.logger import logger


async def run_sandboxed_runtime(socket_path: str) -> None:
    """Entry point for sandboxed runtime execution.

    Connects to orchestrator socket, receives init payload, instantiates
    the runtime, and runs the agent.

    Args:
        socket_path: Path to the orchestrator's Unix socket.
    """
    logger.info("Connecting to orchestrator socket", socket_path=socket_path)

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
        runtime = ClaudeAgentRuntime(socket_writer)
        await runtime.run(payload)

    except Exception as e:
        logger.exception("Runtime error", error=str(e))
        await socket_writer.send_error(str(e))
        raise
    finally:
        await socket_writer.send_done()
        await socket_writer.close()


def main() -> None:
    """CLI entry point for sandboxed runtime."""
    parser = argparse.ArgumentParser(description="Tracecat Agent Runtime (sandboxed)")
    parser.add_argument(
        "--socket",
        required=True,
        help="Path to orchestrator Unix socket",
    )
    args = parser.parse_args()

    asyncio.run(run_sandboxed_runtime(args.socket))


if __name__ == "__main__":
    main()

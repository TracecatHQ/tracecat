"""Base runtime protocol for sandboxed agent execution."""

from __future__ import annotations

from typing import Protocol

from tracecat.agent.common.protocol import RuntimeInitPayload
from tracecat.agent.common.socket_io import SocketStreamWriter


class BaseRuntime(Protocol):
    """Protocol for agent runtimes.

    All runtimes must implement this interface to be loadable
    by the sandbox entrypoint.
    """

    def __init__(self, socket_writer: SocketStreamWriter) -> None:
        """Initialize the runtime with a socket writer.

        Args:
            socket_writer: Writer for sending events to the orchestrator.
        """
        ...

    async def run(self, payload: RuntimeInitPayload) -> None:
        """Run the agent with the given initialization payload.

        Args:
            payload: The initialization payload from the orchestrator.
        """
        ...

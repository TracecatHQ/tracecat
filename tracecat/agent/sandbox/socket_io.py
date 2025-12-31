"""Socket I/O utilities for sandboxed runtime.

Provides stream writing capabilities over Unix sockets for the runtime
to communicate with the orchestrator.
"""

from __future__ import annotations

import asyncio
from dataclasses import asdict

import orjson
from pydantic_core import to_jsonable_python

from tracecat.agent.sandbox.protocol import RuntimeEventEnvelope
from tracecat.agent.stream.types import UnifiedStreamEvent


class SocketStreamWriter:
    """Writes events to orchestrator via Unix socket.

    This is used by the sandboxed runtime to stream events back to the
    trusted orchestrator. Each message is sent as a newline-delimited JSON.
    """

    def __init__(self, writer: asyncio.StreamWriter):
        self._writer = writer

    async def _send(self, envelope: RuntimeEventEnvelope) -> None:
        """Send an envelope over the socket."""
        data = orjson.dumps(
            asdict(envelope),
            default=to_jsonable_python,
        )
        self._writer.write(data + b"\n")
        await self._writer.drain()

    async def send_event(self, event: UnifiedStreamEvent) -> None:
        """Send a stream event to the orchestrator."""
        await self._send(RuntimeEventEnvelope.from_event(event))

    async def send_session_update(
        self,
        sdk_session_id: str,
        sdk_session_data: str,
    ) -> None:
        """Send session update for persistence by orchestrator."""
        await self._send(
            RuntimeEventEnvelope.from_session_update(sdk_session_id, sdk_session_data)
        )

    async def send_error(self, error: str) -> None:
        """Send error event to the orchestrator."""
        await self._send(RuntimeEventEnvelope.from_error(error))

    async def send_done(self) -> None:
        """Send done event to signal completion."""
        await self._send(RuntimeEventEnvelope.done())

    async def close(self) -> None:
        """Close the socket connection."""
        self._writer.close()
        await self._writer.wait_closed()

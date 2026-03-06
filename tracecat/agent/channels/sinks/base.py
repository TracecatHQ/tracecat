"""Base protocol for external channel sinks."""

from __future__ import annotations

from typing import Protocol, runtime_checkable

from tracecat.agent.common.stream_types import UnifiedStreamEvent


@runtime_checkable
class ExternalChannelSink(Protocol):
    """Sink interface used by LoopbackHandler for external channels."""

    async def append(self, event: UnifiedStreamEvent) -> None:
        """Append a runtime stream event."""

    async def error(self, error: str) -> None:
        """Emit a terminal error."""

    async def done(self) -> None:
        """Emit a terminal completion signal."""

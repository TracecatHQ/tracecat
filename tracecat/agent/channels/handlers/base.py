"""Protocol for external channel event handlers."""

from __future__ import annotations

from typing import Any, Protocol

from tracecat.agent.channels.schemas import ValidatedChannelToken


class ExternalChannelHandler(Protocol):
    """Handler protocol for channel-specific event processing."""

    async def handle(
        self, *, payload: dict[str, Any], token: ValidatedChannelToken
    ) -> None:
        """Process a validated external channel event payload."""

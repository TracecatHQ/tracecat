from enum import StrEnum


class UserMessageOrigin(StrEnum):
    """Where a user message entered the session."""

    # Sent inside Tracecat: chat, API, MCP, workflows.
    TRACECAT = "tracecat"
    # Written by channel ingress; only these rows may hold a channel message.
    CHANNEL = "channel"

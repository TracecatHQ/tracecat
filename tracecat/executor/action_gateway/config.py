"""Action gateway configuration helpers."""

from __future__ import annotations

from pathlib import Path

from tracecat import config

ACTION_GATEWAY_SANDBOX_SOCKET = Path("/var/run/tracecat/action-gateway.sock")
"""Path actions use inside nsjail after the host action gateway socket is mounted."""


def action_gateway_socket_path() -> Path | None:
    """Return the host-side action gateway Unix socket path when enabled."""
    if not config.TRACECAT__ACTION_GATEWAY_ENABLED:
        return None
    return Path(config.TRACECAT__ACTION_GATEWAY_SOCKET)

"""Shared constants for agent execution limits."""

AGENT_TIMEOUT_SECONDS_MIN = 5
"""Minimum configurable active agent runtime in seconds."""

AGENT_TIMEOUT_SECONDS_DEFAULT = 1800
"""Default configurable active agent runtime in seconds (30 minutes)."""

AGENT_TIMEOUT_SECONDS_MAX = 3600
"""Maximum configurable active agent runtime in seconds (one hour)."""

AGENT_TIMEOUT_CLEANUP_BUFFER_SECONDS = 60
"""Extra infrastructure time for cancellation and terminal result persistence."""

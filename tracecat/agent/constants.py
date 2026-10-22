"""Shared constants for agent execution limits."""

AGENT_TIMEOUT_SECONDS_DEFAULT = 10800
"""Default agent runtime in seconds (3 hours); also the clamp floor."""

AGENT_SANDBOX_TIMEOUT_SECONDS_DEFAULT = 10800
"""Default agent timeout ceiling in seconds (3 hours)."""

AGENT_TIMEOUT_CLEANUP_BUFFER_SECONDS = 60
"""Extra infrastructure time for cancellation and terminal result persistence."""

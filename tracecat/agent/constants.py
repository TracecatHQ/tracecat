"""Shared constants for agent execution limits."""

AGENT_TIMEOUT_SECONDS_DEFAULT = 7200
"""Default agent runtime in seconds (2 hours) for actions and chat turns."""

AGENT_TIMEOUT_SECONDS_FLOOR = 1800
"""Minimum explicit agent runtime in seconds (30 minutes)."""

AGENT_SANDBOX_TIMEOUT_SECONDS_DEFAULT = 10800
"""Default agent timeout ceiling in seconds (3 hours)."""

AGENT_TIMEOUT_CLEANUP_BUFFER_SECONDS = 60
"""Extra infrastructure time for cancellation and terminal result persistence."""

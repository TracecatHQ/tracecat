"""Shared constants for agent execution limits."""

AGENT_TIMEOUT_SECONDS_DEFAULT = 1800
"""Fallback for the deployment default agent runtime in seconds (30 minutes)."""

AGENT_TIMEOUT_SECONDS_MAX = 3600
"""Fallback for the deployment cap on agent runtime in seconds (one hour)."""

AGENT_TIMEOUT_CLEANUP_BUFFER_SECONDS = 60
"""Extra infrastructure time for cancellation and terminal result persistence."""

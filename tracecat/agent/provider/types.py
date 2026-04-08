"""Domain types for LLM provider management."""

from enum import StrEnum


class AgentCustomProviderDiscoveryStatus(StrEnum):
    """Discovery lifecycle states for custom provider catalog refreshes."""

    NEVER = "never"
    RUNNING = "running"
    SUCCEEDED = "succeeded"
    FAILED = "failed"

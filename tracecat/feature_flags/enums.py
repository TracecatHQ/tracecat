"""Feature flag identifiers used for controlled engineering rollouts.

Boundary note:
- ``workflow-concurrency-limits`` gates tier and organization concurrency
  enforcement only (workflow permits, action permits, and per-workflow action
  execution budget).
- Engine backpressure controls are independent and always on:
  ``TRACECAT__CHILD_WORKFLOW_DISPATCH_WINDOW`` and
  ``TRACECAT__DSL_SCHEDULER_MAX_PENDING_TASKS``.
"""

from enum import StrEnum


class FeatureFlag(StrEnum):
    """Feature flag enum reserved for engineering rollouts."""

    AI_RANKING = "ai-ranking"
    WORKFLOW_CONCURRENCY_LIMITS = "workflow-concurrency-limits"
    AGENT_CHANNELS = "agent-channels"

"""Temporal priorities for work scheduled on the shared agent queue."""

from temporalio.common import Priority

INTERACTIVE_AGENT_WORKFLOW_PRIORITY = Priority(priority_key=1)
"""Priority for latency-sensitive interactive agent turns.

Headless/DSL agent runs on the same queue start with the server default
(priority_key 3), so interactive turns win under queue contention.
"""


def resolve_interactive_agent_workflow_priority(*, enabled: bool) -> Priority:
    """Return interactive priority only for clusters configured to honor it."""
    if enabled:
        return INTERACTIVE_AGENT_WORKFLOW_PRIORITY
    return Priority()

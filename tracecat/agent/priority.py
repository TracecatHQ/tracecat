"""Temporal priorities for work scheduled on the shared agent queue."""

from temporalio.common import Priority

INTERACTIVE_AGENT_WORKFLOW_PRIORITY = Priority(priority_key=1)
"""Priority for latency-sensitive interactive agent turns."""

BACKGROUND_AGENT_WORKFLOW_PRIORITY = Priority(priority_key=3)
"""Priority for headless agent turns started by DSL workflows."""

"""Backend-independent identity for a Tracecat agent turn."""

from uuid import UUID


def agent_workflow_id(run_id: UUID) -> str:
    """Return the Temporal workflow ID for a logical agent run."""
    return f"agent/{run_id}"

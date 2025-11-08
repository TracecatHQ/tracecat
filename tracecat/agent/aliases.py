from __future__ import annotations

from tracecat.identifiers.workflow import WorkflowID, WorkflowUUID


def _normalize_workflow_id_for_alias(
    workflow_id: WorkflowID | str | None,
) -> str:
    """Convert any workflow identifier to a short slug for alias construction."""
    if workflow_id is None:
        return "unknown"
    try:
        wf_uuid = WorkflowUUID.new(workflow_id)
    except Exception:
        return str(workflow_id)
    return wf_uuid.short()


def build_agent_alias(
    workflow_id: WorkflowID | str | None,
    action_ref: str,
) -> str:
    """Construct the Tracecat alias for agent workflows."""
    workflow_part = _normalize_workflow_id_for_alias(workflow_id)
    action_part = action_ref or "unknown_action"
    return f"agent:{workflow_part}:{action_part}"

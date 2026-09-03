from __future__ import annotations

from uuid import UUID, uuid4

from pydantic import BaseModel
from temporalio.common import Priority

from tracecat.auth.types import Role
from tracecat.dsl.schemas import RunActionInput

AGENT_TOOL_PRIORITY = Priority(priority_key=2)
"""Priority for tool execution activities. This is higher than the default priority (1) but lower than the priority for agent execution activities (3)."""
AGENT_TOOL_WORKFLOW_PREFIX = "agent-tool"


def build_agent_tool_workflow_id() -> str:
    """Build a fresh workflow ID for a registry tool execution."""
    return f"{AGENT_TOOL_WORKFLOW_PREFIX}/{uuid4()}"


class ExecuteRegistryToolWorkflowInput(BaseModel):
    """Workflow input for routing a single registry UDF to the executor queue."""

    run_input: RunActionInput
    role: Role


class ExecuteRegistryToolWorkflowMemo(BaseModel):
    """Correlation metadata stored on registry tool workflow executions."""

    parent_agent_workflow_id: str | None = None
    parent_agent_run_id: str | None = None
    parent_agent_session_id: UUID
    tool_call_id: str | None = None
    action_name: str

from __future__ import annotations

from pydantic import BaseModel
from temporalio.common import Priority

from tracecat.auth.types import Role
from tracecat.dsl.schemas import RunActionInput

AGENT_TOOL_PRIORITY = Priority(priority_key=2)


class ExecuteRegistryToolWorkflowInput(BaseModel):
    """Workflow input for routing a single registry UDF to the executor queue."""

    run_input: RunActionInput
    role: Role

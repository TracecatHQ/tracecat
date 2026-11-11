"""Temporal payloads for running workflow agent actions on an agent backend."""

import uuid
from typing import Any

from pydantic import BaseModel, Field

from tracecat.agent.backends.schemas import AgentWorkflowArgs


def workflow_agent_turn_workflow_id(run_id: uuid.UUID) -> str:
    """Return the stable workflow ID of the parent of one backend turn."""
    return f"workflow-agent-turn/{run_id}"


class SelectWorkflowAgentBackendResult(BaseModel):
    # None when the action runs on the built-in backend: the workflow then
    # starts the built-in durable workflow with the same arguments.
    backend_id: str | None = None


class WorkflowAgentTurnInput(BaseModel):
    """A workflow agent action's turn on an installed backend.

    The input of the turn's parent workflow, and of the activity that prepares
    the turn there.
    """

    # Minted by the action, so a retry adopts this action's own reservation
    # and a concurrent action on the same session is refused.
    run_id: uuid.UUID
    # The backend the action selected; preparation refuses any other.
    backend_id: str
    # The action's own arguments, as the built-in workflow would take them,
    # run views included.
    args: AgentWorkflowArgs


class ReadWorkflowAgentTurnInput(BaseModel):
    backend_id: str
    # The child's decoded result, read by its backend.
    result: Any = Field(default=None)


class ReadWorkflowAgentTurnResult(BaseModel):
    output: Any = Field(default=None)
    # The turn ended without a usable output, for example when it failed or
    # was cancelled.
    error: str | None = Field(default=None)

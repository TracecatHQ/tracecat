"""Temporal payloads for running workflow agent actions on an agent backend."""

import uuid
from typing import Any

from pydantic import BaseModel, Field

from tracecat.agent.backends.schemas import AgentWorkflowArgs, ChildAgentTurn
from tracecat.auth.types import Role

WORKFLOW_AGENT_TURN_WORKFLOW = "WorkflowAgentTurnWorkflow"


def workflow_agent_turn_workflow_id(run_id: uuid.UUID) -> str:
    """Return the stable workflow ID of the parent of one backend turn."""
    return f"workflow-agent-turn/{run_id}"


class PrepareWorkflowAgentTurnInput(BaseModel):
    role: Role
    # Minted by the action, so a retry adopts this action's own reservation
    # and a concurrent action on the same session is refused.
    run_id: uuid.UUID
    # The built-in workflow's input, which the action builds either way. Its
    # session, prompt, configuration and limits describe the turn on any
    # backend.
    args: AgentWorkflowArgs


class PrepareWorkflowAgentTurnResult(BaseModel):
    # None when the action runs on the built-in backend: the workflow then
    # starts the built-in durable workflow with the same arguments.
    turn: ChildAgentTurn | None = None


class ReadWorkflowAgentTurnInput(BaseModel):
    backend_id: str
    # The child's decoded result, read by its backend.
    result: Any = Field(default=None)


class ReadWorkflowAgentTurnResult(BaseModel):
    output: Any = Field(default=None)
    # The turn ended without a usable output, for example when it failed or
    # was cancelled.
    error: str | None = Field(default=None)


class WorkflowAgentTurnInput(BaseModel):
    """A prepared workflow agent action turn on an installed backend."""

    turn: ChildAgentTurn
    # The action's own arguments, as the built-in workflow would take them.
    # Run views show these, so both paths read the same.
    args: AgentWorkflowArgs

"""Temporal payloads for running workflow agent actions on an agent backend."""

from typing import Any

from pydantic import BaseModel, Field

from tracecat.agent.backends.schemas import AgentWorkflowArgs, ChildAgentTurn
from tracecat.auth.types import Role


class PrepareWorkflowAgentTurnInput(BaseModel):
    role: Role
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

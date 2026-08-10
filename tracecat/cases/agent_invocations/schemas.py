"""Temporal payloads for case-comment agent invocation orchestration."""

from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel
from tracecat_ee.agent.workflows.durable import AgentWorkflowArgs

from tracecat.auth.types import Role

PREPARE_COMMENT_AGENT_INVOCATION_ACTIVITY = "prepare_comment_agent_invocation_activity"
COMPLETE_COMMENT_AGENT_INVOCATION_ACTIVITY = (
    "complete_comment_agent_invocation_activity"
)
FAIL_COMMENT_AGENT_INVOCATION_ACTIVITY = "fail_comment_agent_invocation_activity"
CASE_COMMENT_AGENT_INVOCATION_WORKFLOW = "CaseCommentAgentInvocationWorkflow"


def comment_agent_invocation_workflow_id(invocation_id: uuid.UUID) -> str:
    """Return the stable Temporal workflow ID for one invocation."""
    return f"case-comment-agent-invocation/{invocation_id}"


class CaseCommentAgentInvocationWorkflowInput(BaseModel):
    role: Role
    invocation_id: uuid.UUID


class PrepareCommentAgentInvocationInput(BaseModel):
    role: Role
    invocation_id: uuid.UUID


class PrepareCommentAgentInvocationResult(BaseModel):
    workflow_args: AgentWorkflowArgs | None = None


class CompleteCommentAgentInvocationInput(BaseModel):
    role: Role
    session_id: uuid.UUID
    run_id: uuid.UUID
    output: Any


class CompleteCommentAgentInvocationResult(BaseModel):
    handled: bool
    reply_comment_id: uuid.UUID | None = None


class FailCommentAgentInvocationInput(BaseModel):
    role: Role
    invocation_id: uuid.UUID
    kind: Literal["preparation", "agent_turn", "completion", "cancelled"]
    error: str


class FailCommentAgentInvocationResult(BaseModel):
    transitioned: bool

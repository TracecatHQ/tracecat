"""Temporal payloads for case-comment agent invocation orchestration."""

from __future__ import annotations

import uuid
from typing import Any

from pydantic import BaseModel
from temporalio.common import RetryPolicy
from tracecat_ee.agent.workflows.durable import AgentWorkflowArgs

from tracecat.auth.types import Role
from tracecat.cases.agent_invocations.types import (
    CaseCommentAgentInvocationErrorKind,
)

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


class CommentAgentTurn(BaseModel):
    """A prepared turn the invocation workflow starts as a child by type name."""

    backend_id: str
    session_id: uuid.UUID
    run_id: uuid.UUID
    workflow_type: str
    workflow_id: str
    task_queue: str
    priority_key: int | None = None
    retry_policy: RetryPolicy
    # The selected backend's own workflow input, kept opaque so this workflow
    # never imports another backend's types. The child decodes it.
    args: Any


class PrepareCommentAgentInvocationResult(BaseModel):
    # Set only by runs recorded before turns went through the backend contract.
    # Their replays must keep starting the built-in workflow directly.
    workflow_args: AgentWorkflowArgs | None = None
    turn: CommentAgentTurn | None = None


class CompleteCommentAgentInvocationInput(BaseModel):
    role: Role
    session_id: uuid.UUID
    run_id: uuid.UUID
    output: Any
    # When set, ``output`` is the child's decoded result, read by this backend.
    backend_id: str | None = None


class CompleteCommentAgentInvocationResult(BaseModel):
    handled: bool
    reply_comment_id: uuid.UUID | None = None
    # The turn ended without a usable output; nothing was posted.
    turn_error: str | None = None


class FailCommentAgentInvocationInput(BaseModel):
    role: Role
    invocation_id: uuid.UUID
    kind: CaseCommentAgentInvocationErrorKind
    error: str


class FailCommentAgentInvocationResult(BaseModel):
    transitioned: bool

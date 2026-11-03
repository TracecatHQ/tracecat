"""Domain types for case trigger delivery."""

from __future__ import annotations

import uuid

from pydantic import BaseModel, Field

from tracecat.identifiers.workflow import WorkflowExecutionID


class CaseCommentWorkflowTrigger(BaseModel):
    """A workflow a user picked when posting a comment.

    Stored in ``case_trigger_outbox.extras``. The run's execution ID is
    assigned when the comment is created, so the comment can link to the run
    before it starts.
    """

    workflow_id: uuid.UUID
    wf_exec_id: WorkflowExecutionID
    comment_id: uuid.UUID
    parent_id: uuid.UUID | None = Field(default=None)
    comment: str
    triggered_by_type: str
    triggered_by_user_id: uuid.UUID | None = Field(default=None)
    triggered_by_service_id: str | None = Field(default=None)

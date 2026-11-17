"""Domain types for case trigger delivery."""

from __future__ import annotations

import re
import uuid
from typing import Any, Self, TypedDict

from pydantic import BaseModel, Field, model_validator

from tracecat.identifiers.workflow import (
    EXEC_ID_SHORT_PATTERN,
    WorkflowExecutionID,
    WorkflowUUID,
)


class CaseTriggerTag(TypedDict):
    """A case tag as case-triggered runs receive it."""

    id: str
    ref: str
    name: str
    color: str | None


class CaseTriggerEvent(TypedDict):
    """The case event a configured case trigger fired on."""

    id: str
    type: str
    # Variant-specific event fields, stored as JSONB; their shape depends on
    # the event type, so they pass through unchanged.
    data: dict[str, Any] | None
    created_at: str
    user_id: str | None
    wf_exec_id: str | None


class CaseTriggerPayload(TypedDict):
    """Trigger inputs for a run started by a configured case trigger."""

    case_id: str
    event: CaseTriggerEvent
    tags: list[CaseTriggerTag]
    workspace_id: str


class CommentWorkflowTriggeredBy(TypedDict):
    type: str
    user_id: str | None
    service_id: str | None


class CommentWorkflowEvent(TypedDict):
    id: str
    type: str
    created_at: str
    user_id: str | None


class CommentWorkflowPayload(TypedDict):
    """Trigger inputs for a workflow a user picked when posting a comment."""

    case_id: str
    comment: str
    comment_id: str
    parent_id: str | None
    thread_root_id: str
    is_reply: bool
    text: str
    workspace_id: str
    triggered_by: CommentWorkflowTriggeredBy
    event: CommentWorkflowEvent
    tags: list[CaseTriggerTag]


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
    settled: bool = Field(
        default=False,
        description=(
            "Set once the dispatcher started the run or recorded its failure, "
            "so retrying the row for another run does not handle it again."
        ),
    )

    @model_validator(mode="after")
    def _exec_id_names_the_workflow(self) -> Self:
        # The execution ID is used as a Temporal workflow ID and a storage key;
        # it must be exactly one of the picked workflow's own run IDs.
        prefix = f"{WorkflowUUID.new(self.workflow_id).short()}/"
        suffix = self.wf_exec_id.removeprefix(prefix)
        if suffix == self.wf_exec_id or not re.fullmatch(EXEC_ID_SHORT_PATTERN, suffix):
            raise ValueError("wf_exec_id does not belong to workflow_id")
        return self

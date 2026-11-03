"""Domain types for case trigger delivery."""

from __future__ import annotations

import re
import uuid
from typing import Self

from pydantic import BaseModel, Field, model_validator

from tracecat.identifiers.workflow import (
    EXEC_ID_SHORT_PATTERN,
    WorkflowExecutionID,
    WorkflowUUID,
)


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

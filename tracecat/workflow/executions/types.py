from __future__ import annotations

from dataclasses import dataclass

from tracecat.dsl.types import ActionErrorInfo
from tracecat.workflow.executions.enums import TriggerType
from tracecat.workflow.identifiers import WorkflowExecutionID, WorkflowID


@dataclass(frozen=True)
class ErrorHandlerWorkflowInput:
    message: str
    handler_wf_id: WorkflowID
    orig_wf_id: WorkflowID
    orig_wf_exec_id: WorkflowExecutionID
    orig_wf_title: str
    trigger_type: TriggerType
    errors: list[ActionErrorInfo] | None = None
    orig_wf_exec_url: str | None = None

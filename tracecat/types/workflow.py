from pydantic import BaseModel

from tracecat.runner.workflows import Workflow
from tracecat.types.api import RunStatus


class WorkflowRunContext(BaseModel):
    workflow_run_id: str
    workflow: Workflow
    status: RunStatus

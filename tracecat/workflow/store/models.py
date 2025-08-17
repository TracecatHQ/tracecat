from pydantic import BaseModel

from tracecat.identifiers.workflow import WorkflowID
from tracecat.store import Source

WorkflowSource = Source[WorkflowID]


class WorkflowDslPublish(BaseModel):
    message: str | None = None

from pydantic import BaseModel

from tracecat.identifiers.workflow import WorkflowID
from tracecat.store import ExternalStore, Source

WorkflowSource = Source[WorkflowID]
WorkflowStore = ExternalStore[WorkflowSource]


class WorkflowDslPublish(BaseModel):
    message: str | None = None

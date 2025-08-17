from pydantic import BaseModel

from tracecat.identifiers.workflow import WorkflowID
from tracecat.store import Source

# TODO(deps): This is only supported starting pydantic 2.11+
WorkflowSource = Source[WorkflowID]


class WorkflowDslPublish(BaseModel):
    message: str | None = None

from pydantic import BaseModel

from tracecat.identifiers import TagID


class WorkflowTagCreate(BaseModel):
    tag_id: TagID

from pydantic import BaseModel

from tracecat.identifiers import TagID


class TagRead(BaseModel):
    id: TagID
    name: str
    color: str | None = None


class TagCreate(BaseModel):
    name: str
    color: str | None = None


class TagUpdate(BaseModel):
    name: str | None = None
    color: str | None = None

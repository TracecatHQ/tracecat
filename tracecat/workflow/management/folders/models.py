import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from tracecat.workflow.management.models import WorkflowReadMinimal


class WorkflowFolderRead(BaseModel):
    id: uuid.UUID
    name: str
    path: str
    owner_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class WorkflowFolderCreate(BaseModel):
    name: str
    parent_path: str = "/"


class WorkflowFolderUpdate(BaseModel):
    name: str | None = None


class WorkflowFolderMove(BaseModel):
    new_parent_path: str | None = None


class WorkflowFolderDelete(BaseModel):
    recursive: bool = False


class FolderDirectoryItem(WorkflowFolderRead):
    type: Literal["folder"]
    num_items: int


class WorkflowDirectoryItem(WorkflowReadMinimal):
    type: Literal["workflow"]


DirectoryItem = Annotated[
    WorkflowDirectoryItem | FolderDirectoryItem,
    Field(discriminator="type"),
]
DirectoryItemAdapter: TypeAdapter[DirectoryItem] = TypeAdapter(DirectoryItem)

"""Pydantic schemas for table folder resources."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from tracecat.core.schemas import Schema


class TableFolderRead(Schema):
    id: uuid.UUID
    name: str
    path: str
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class TableFolderCreate(BaseModel):
    name: str
    parent_path: str = "/"


class TableFolderUpdate(BaseModel):
    name: str | None = None


class TableFolderMove(BaseModel):
    new_parent_path: str | None = None


class TableFolderDelete(BaseModel):
    recursive: bool = False


class TableFolderDirectoryItem(TableFolderRead):
    type: Literal["folder"]
    num_items: int


class TableDirectoryItem(BaseModel):
    """Table as a directory item."""

    type: Literal["table"]
    id: uuid.UUID
    name: str
    folder_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime


DirectoryItem = Annotated[
    TableDirectoryItem | TableFolderDirectoryItem,
    Field(discriminator="type"),
]
DirectoryItemAdapter: TypeAdapter[DirectoryItem] = TypeAdapter(DirectoryItem)

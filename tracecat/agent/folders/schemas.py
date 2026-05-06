"""Pydantic schemas for agent folder resources."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from tracecat.tags.schemas import TagRead


class AgentFolderRead(BaseModel):
    id: uuid.UUID
    name: str
    path: str
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class AgentFolderCreate(BaseModel):
    name: str
    parent_path: str = "/"


class AgentFolderUpdate(BaseModel):
    name: str | None = None


class AgentFolderMove(BaseModel):
    new_parent_path: str | None = None


class AgentFolderDelete(BaseModel):
    recursive: bool = False


class AgentFolderDirectoryItem(AgentFolderRead):
    type: Literal["folder"]
    num_items: int


class AgentPresetDirectoryItem(BaseModel):
    """Agent preset as a directory item."""

    type: Literal["preset"]
    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    model_provider: str
    model_name: str
    folder_id: uuid.UUID | None
    tags: list[TagRead]
    created_at: datetime
    updated_at: datetime


DirectoryItem = Annotated[
    AgentPresetDirectoryItem | AgentFolderDirectoryItem,
    Field(discriminator="type"),
]
DirectoryItemAdapter: TypeAdapter[DirectoryItem] = TypeAdapter(DirectoryItem)

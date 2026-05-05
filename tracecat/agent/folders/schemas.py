"""Pydantic schemas for agent folders and the directory listing."""

from __future__ import annotations

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
    name: str = Field(min_length=1, max_length=120)
    parent_path: str = "/"


class AgentFolderUpdate(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)


class AgentFolderMove(BaseModel):
    new_parent_path: str | None = None


class AgentFolderDelete(BaseModel):
    recursive: bool = False


class AgentFolderDirectoryItem(AgentFolderRead):
    type: Literal["folder"]
    num_items: int


class AgentPresetDirectoryItem(BaseModel):
    type: Literal["preset"]
    id: uuid.UUID
    name: str
    slug: str
    description: str | None = None
    model_provider: str
    model_name: str
    folder_id: uuid.UUID | None = None
    tags: list[TagRead] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


DirectoryItem = Annotated[
    AgentFolderDirectoryItem | AgentPresetDirectoryItem,
    Field(discriminator="type"),
]
DirectoryItemAdapter: TypeAdapter[DirectoryItem] = TypeAdapter(DirectoryItem)

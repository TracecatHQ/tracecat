"""Pydantic schemas for skill folder resources."""

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field, TypeAdapter

from tracecat.core.schemas import Schema
from tracecat.tags.schemas import TagRead


class SkillFolderRead(Schema):
    id: uuid.UUID
    name: str
    path: str
    workspace_id: uuid.UUID
    created_at: datetime
    updated_at: datetime


class SkillFolderCreate(BaseModel):
    name: str
    parent_path: str = "/"


class SkillFolderUpdate(BaseModel):
    name: str | None = None


class SkillFolderMove(BaseModel):
    new_parent_path: str | None = None


class SkillFolderDelete(BaseModel):
    recursive: bool = False


class SkillFolderDirectoryItem(SkillFolderRead):
    type: Literal["folder"]
    num_items: int


class SkillDirectoryItem(BaseModel):
    """Skill as a directory item."""

    type: Literal["skill"]
    id: uuid.UUID
    name: str
    slug: str
    description: str | None
    current_version_id: uuid.UUID | None
    folder_id: uuid.UUID | None
    tags: list[TagRead]
    created_at: datetime
    updated_at: datetime


DirectoryItem = Annotated[
    SkillDirectoryItem | SkillFolderDirectoryItem,
    Field(discriminator="type"),
]
DirectoryItemAdapter: TypeAdapter[DirectoryItem] = TypeAdapter(DirectoryItem)

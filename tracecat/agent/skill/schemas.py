"""Pydantic schemas for workspace skills."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from tracecat.core.schemas import Schema
from tracecat.identifiers import WorkspaceID

SkillSlug = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=160),
]
SkillPath = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1024),
]


class SkillVersionSummary(Schema):
    """Compact metadata for the current published skill version."""

    id: uuid.UUID
    version: int
    manifest_sha256: str
    file_count: int
    total_size_bytes: int
    title: str | None = Field(default=None)
    description: str | None = Field(default=None)
    created_at: datetime
    updated_at: datetime


class SkillValidationErrorDetail(BaseModel):
    """Structured draft validation error."""

    code: str
    message: str
    path: str | None = None


class SkillFileEntry(Schema):
    """Manifest entry for a skill file (used in both drafts and versions)."""

    path: str
    blob_id: uuid.UUID
    sha256: str
    size_bytes: int
    content_type: str


class SkillRead(Schema):
    """Full response model for a workspace skill."""

    id: uuid.UUID
    workspace_id: WorkspaceID
    slug: str
    title: str | None = Field(default=None)
    description: str | None = Field(default=None)
    current_version_id: uuid.UUID | None = Field(default=None)
    draft_revision: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = Field(default=None)
    current_version: SkillVersionSummary | None = Field(default=None)
    is_draft_publishable: bool
    draft_validation_errors: list[SkillValidationErrorDetail] = Field(
        default_factory=list
    )
    draft_file_count: int


class SkillCreate(Schema):
    """Payload for creating a new logical skill."""

    slug: SkillSlug
    title: str | None = Field(default=None, max_length=255)
    description: str | None = Field(default=None, max_length=4000)


class SkillUploadFile(Schema):
    """Single file in a one-shot skill upload payload."""

    path: SkillPath
    content_base64: str
    content_type: str | None = Field(default=None, max_length=255)


class SkillUpload(Schema):
    """Payload for importing a full skill draft in one request."""

    slug: SkillSlug
    files: list[SkillUploadFile] = Field(min_length=1)


class SkillDraftRead(Schema):
    """Current mutable draft state for a skill."""

    skill_id: uuid.UUID
    skill_slug: str
    draft_revision: int
    title: str | None = Field(default=None)
    description: str | None = Field(default=None)
    files: list[SkillFileEntry] = Field(default_factory=list)
    is_publishable: bool
    validation_errors: list[SkillValidationErrorDetail] = Field(default_factory=list)


class SkillDraftFileRead(Schema):
    """Response model for reading a single skill draft file."""

    kind: Literal["inline", "download"]
    path: str
    content_type: str
    size_bytes: int
    sha256: str
    text_content: str | None = Field(default=None)
    download_url: str | None = Field(default=None)


class SkillUploadSessionCreate(Schema):
    """Request body for creating a staged draft upload."""

    sha256: Annotated[
        str, StringConstraints(strip_whitespace=True, min_length=64, max_length=64)
    ]
    size_bytes: int = Field(gt=0)
    content_type: str = Field(min_length=1, max_length=255)


class SkillUploadSessionRead(Schema):
    """Presigned upload session details for a draft file blob."""

    upload_id: uuid.UUID
    upload_url: str
    method: Literal["PUT"] = "PUT"
    headers: dict[str, str] = Field(default_factory=dict)
    expires_at: datetime
    bucket: str
    key: str


class SkillDraftUpsertTextFileOp(BaseModel):
    """Replace or create a text file in the skill draft."""

    op: Literal["upsert_text_file"] = "upsert_text_file"
    path: SkillPath
    content: str
    content_type: str = Field(default="text/plain; charset=utf-8", max_length=255)


class SkillDraftAttachUploadedBlobOp(BaseModel):
    """Attach a finalized staged upload to a draft path."""

    op: Literal["attach_uploaded_blob"] = "attach_uploaded_blob"
    path: SkillPath
    upload_id: uuid.UUID


class SkillDraftDeleteFileOp(BaseModel):
    """Delete a file from the mutable skill draft."""

    op: Literal["delete_file"] = "delete_file"
    path: SkillPath


type SkillDraftOperation = Annotated[
    SkillDraftUpsertTextFileOp
    | SkillDraftAttachUploadedBlobOp
    | SkillDraftDeleteFileOp,
    Field(discriminator="op"),
]


class SkillDraftPatch(Schema):
    """Optimistic-concurrency draft mutation request."""

    base_revision: int = Field(ge=0)
    operations: list[SkillDraftOperation] = Field(min_length=1)


class SkillVersionRead(Schema):
    """Published skill version response including its manifest."""

    id: uuid.UUID
    skill_id: uuid.UUID
    workspace_id: WorkspaceID
    version: int
    manifest_sha256: str
    file_count: int
    total_size_bytes: int
    title: str | None = Field(default=None)
    description: str | None = Field(default=None)
    created_at: datetime
    updated_at: datetime
    files: list[SkillFileEntry] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)

"""Pydantic schemas for workspace skills."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

from pydantic import (
    AfterValidator,
    BaseModel,
    ConfigDict,
    Field,
    StringConstraints,
    field_validator,
)

from tracecat.core.schemas import Schema
from tracecat.identifiers import WorkspaceID


def _validate_skill_name(value: str) -> str:
    if value.startswith("-") or value.endswith("-"):
        raise ValueError("Skill name must not start or end with a hyphen")
    if "--" in value:
        raise ValueError("Skill name must not contain consecutive hyphens")
    return value


SkillName = Annotated[
    str,
    StringConstraints(
        strip_whitespace=True,
        min_length=1,
        max_length=64,
        pattern=r"^[a-z0-9-]+$",
    ),
    AfterValidator(_validate_skill_name),
]
SkillPath = Annotated[
    str,
    StringConstraints(strip_whitespace=True, min_length=1, max_length=1024),
]


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
    name: str
    description: str | None = Field(default=None)
    current_version_id: uuid.UUID | None = Field(default=None)
    draft_revision: int
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = Field(default=None)
    current_version: SkillVersionReadMinimal | None = Field(default=None)
    is_draft_publishable: bool
    draft_validation_errors: list[SkillValidationErrorDetail] = Field(
        default_factory=list
    )
    draft_file_count: int


class SkillReadMinimal(Schema):
    """Minimal response model for listing workspace skills."""

    id: uuid.UUID
    workspace_id: WorkspaceID
    name: str
    description: str | None = Field(default=None)
    current_version_id: uuid.UUID | None = Field(default=None)
    created_at: datetime
    updated_at: datetime
    archived_at: datetime | None = Field(default=None)


class SkillCreate(Schema):
    """Payload for creating a new logical skill."""

    name: SkillName
    description: str | None = Field(default=None, max_length=4000)


class SkillUploadFile(Schema):
    """Single file in a one-shot skill upload payload."""

    path: SkillPath
    content_base64: str
    content_type: str | None = Field(default=None, max_length=255)


class SkillUpload(Schema):
    """Payload for importing a full skill draft in one request."""

    name: SkillName
    files: list[SkillUploadFile] = Field(min_length=1)


class SkillDraftRead(Schema):
    """Current mutable draft state for a skill."""

    skill_id: uuid.UUID
    skill_name: str
    draft_revision: int
    name: str | None = Field(default=None)
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
        str,
        StringConstraints(
            strip_whitespace=True,
            min_length=64,
            max_length=64,
            pattern=r"^[0-9a-fA-F]{64}$",
        ),
    ]
    size_bytes: int = Field(gt=0)
    content_type: str = Field(min_length=1, max_length=255)

    @field_validator("sha256", mode="before")
    @classmethod
    def normalize_sha256(cls, value: str) -> str:
        """Canonicalize SHA-256 hex digests before persistence."""

        return value.lower() if isinstance(value, str) else value


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
    name: str
    description: str | None = Field(default=None)
    created_at: datetime
    updated_at: datetime
    files: list[SkillFileEntry] = Field(default_factory=list)

    model_config = ConfigDict(from_attributes=True)


class SkillVersionReadMinimal(Schema):
    """Summary response model for published skill versions in list endpoints."""

    id: uuid.UUID
    skill_id: uuid.UUID
    workspace_id: WorkspaceID
    version: int
    manifest_sha256: str
    file_count: int
    total_size_bytes: int
    name: str
    description: str | None = Field(default=None)
    created_at: datetime
    updated_at: datetime

    model_config = ConfigDict(from_attributes=True)

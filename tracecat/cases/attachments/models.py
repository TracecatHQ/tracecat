"""Models for case attachments."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Annotated, Literal

import annotated_types
from pydantic import BaseModel, Field

from tracecat import config
from tracecat.cases.enums import CaseEventType


class CaseAttachmentCreate(BaseModel):
    """Model for creating a case attachment."""

    file_name: str = Field(
        ...,
        max_length=config.TRACECAT__MAX_ATTACHMENT_FILENAME_LENGTH,
        description="Original filename",
    )
    content_type: str = Field(..., max_length=255, description="MIME type of the file")
    size: int = Field(
        ...,
        gt=0,
        le=config.TRACECAT__MAX_ATTACHMENT_SIZE_BYTES,
        description="File size in bytes",
    )
    content: Annotated[
        bytes,
        annotated_types.Len(
            min_length=1, max_length=config.TRACECAT__MAX_ATTACHMENT_SIZE_BYTES
        ),
    ] = Field(..., description="File content")


class CaseAttachmentRead(BaseModel):
    """Model for reading a case attachment."""

    id: uuid.UUID
    case_id: uuid.UUID
    file_id: uuid.UUID
    file_name: str
    content_type: str
    size: int
    sha256: str
    created_at: datetime
    updated_at: datetime
    creator_id: uuid.UUID | None = None
    is_deleted: bool = False


class CaseAttachmentDownloadResponse(BaseModel):
    """Model for attachment download URL response."""

    download_url: str = Field(..., description="Pre-signed download URL")
    file_name: str = Field(..., description="Original filename")
    content_type: str = Field(..., description="MIME type of the file")


class CaseAttachmentDownloadData(BaseModel):
    file_name: str
    content_type: str
    content_base64: str


class FileRead(BaseModel):
    """Model for reading file metadata."""

    id: uuid.UUID
    sha256: str
    name: str
    content_type: str
    size: int
    creator_id: uuid.UUID | None
    created_at: datetime
    updated_at: datetime
    deleted_at: datetime | None = None
    is_deleted: bool


# Attachment Event Models


class AttachmentCreatedEvent(BaseModel):
    type: Literal[CaseEventType.ATTACHMENT_CREATED] = CaseEventType.ATTACHMENT_CREATED
    attachment_id: uuid.UUID
    file_name: str
    content_type: str
    size: int
    wf_exec_id: str | None = Field(
        default=None,
        description="The execution ID of the workflow that triggered the event.",
    )


class AttachmentDeletedEvent(BaseModel):
    type: Literal[CaseEventType.ATTACHMENT_DELETED] = CaseEventType.ATTACHMENT_DELETED
    attachment_id: uuid.UUID
    file_name: str
    wf_exec_id: str | None = Field(
        default=None,
        description="The execution ID of the workflow that triggered the event.",
    )


class AttachmentCreatedEventRead(BaseModel):
    """Event for when an attachment is created for a case."""

    type: Literal[CaseEventType.ATTACHMENT_CREATED] = CaseEventType.ATTACHMENT_CREATED
    attachment_id: uuid.UUID
    file_name: str
    content_type: str
    size: int
    wf_exec_id: str | None = None
    user_id: uuid.UUID | None = Field(
        default=None, description="The user who performed the action."
    )
    created_at: datetime = Field(..., description="The timestamp of the event.")


class AttachmentDeletedEventRead(BaseModel):
    """Event for when an attachment is deleted from a case."""

    type: Literal[CaseEventType.ATTACHMENT_DELETED] = CaseEventType.ATTACHMENT_DELETED
    attachment_id: uuid.UUID
    file_name: str
    wf_exec_id: str | None = None
    user_id: uuid.UUID | None = Field(
        default=None, description="The user who performed the action."
    )
    created_at: datetime = Field(..., description="The timestamp of the event.")

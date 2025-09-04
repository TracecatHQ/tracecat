"""Case attachments module."""

from tracecat.cases.attachments.models import (
    AttachmentCreatedEvent,
    AttachmentCreatedEventRead,
    AttachmentDeletedEvent,
    AttachmentDeletedEventRead,
    CaseAttachmentCreate,
    CaseAttachmentDownloadData,
    CaseAttachmentDownloadResponse,
    CaseAttachmentRead,
)
from tracecat.cases.attachments.service import CaseAttachmentService

__all__ = [
    # Models
    "CaseAttachmentCreate",
    "CaseAttachmentRead",
    "CaseAttachmentDownloadResponse",
    "CaseAttachmentDownloadData",
    "AttachmentCreatedEvent",
    "AttachmentDeletedEvent",
    "AttachmentCreatedEventRead",
    "AttachmentDeletedEventRead",
    # Service
    "CaseAttachmentService",
]

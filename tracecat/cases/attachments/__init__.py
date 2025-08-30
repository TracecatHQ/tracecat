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
    FileRead,
)
from tracecat.cases.attachments.router import attachments_router
from tracecat.cases.attachments.service import CaseAttachmentService

__all__ = [
    # Models
    "CaseAttachmentCreate",
    "CaseAttachmentRead",
    "CaseAttachmentDownloadResponse",
    "CaseAttachmentDownloadData",
    "FileRead",
    "AttachmentCreatedEvent",
    "AttachmentDeletedEvent",
    "AttachmentCreatedEventRead",
    "AttachmentDeletedEventRead",
    # Service
    "CaseAttachmentService",
    # Router
    "attachments_router",
]

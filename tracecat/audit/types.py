from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from tracecat.audit.enums import AuditEventActor, AuditEventStatus

AuditAction = Literal["create", "update", "delete", "accept", "revoke"]
AuditResourceType = Literal[
    "workspace",
    "workflow",
    "workflow_execution",
    "workspace_variable",
    "tag",
    "table",
    "table_column",
    "organization_setting",
    "secret",
    "organization_secret",
    "case",
    "agent_preset",
    "agent_session",
    "organization_member",
    "organization_session",
    "organization_invitation",
    "workspace_invitation",
]


class AuditEvent(BaseModel):
    organization_id: uuid.UUID | None = None
    """Organization ID. None for platform-level operations (superuser without org context)."""
    workspace_id: uuid.UUID | None = None
    actor_type: AuditEventActor
    actor_id: uuid.UUID
    actor_label: str | None = None
    ip_address: str | None = None
    resource_type: AuditResourceType
    resource_id: uuid.UUID | None = None
    action: AuditAction
    status: AuditEventStatus
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

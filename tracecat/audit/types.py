from __future__ import annotations

import uuid
from datetime import UTC, datetime
from typing import Any, Literal

from pydantic import BaseModel, Field

from tracecat.audit.enums import AuditEventActor, AuditEventStatus

AuditSink = Literal["organization", "platform"]
AuditSource = Literal["api", "mcp"]
AuditAction = Literal[
    "create",
    "update",
    "delete",
    "read",
    "commit",
    "publish",
    "restore",
    "connect",
    "disconnect",
    "cancel",
    "fork",
    "rotate",
    "accept",
    "revoke",
    "sign_in",
    "sync",
    "promote",
    "demote",
]
AuditResourceType = Literal[
    "user",
    "organization",
    "auth",
    "workspace",
    "workflow",
    "workflow_execution",
    "schedule",
    "webhook",
    "integration",
    "mcp_integration",
    "workspace_variable",
    "tag",
    "table",
    "table_column",
    "organization_setting",
    "secret",
    "organization_secret",
    "case",
    "case_comment",
    "case_attachment",
    "case_field",
    "case_task",
    "case_tag",
    "case_linked_row",
    "case_dropdown",
    "case_duration",
    "agent_catalog",
    "agent_custom_provider",
    "agent_model_access",
    "agent_preset",
    "agent_session",
    "organization_domain",
    "organization_member",
    "organization_session",
    "organization_invitation",
    "organization_tier",
    "workspace_invitation",
    "service_account",
    "service_account_api_key",
    "mcp_personal_access_token",
    # RBAC resources
    "rbac_scope",
    "rbac_role",
    "rbac_group",
    "rbac_group_member",
    "rbac_assignment",
    "rbac_user_assignment",
    # Platform resources
    "platform_setting",
    "platform_registry",
    "platform_registry_repository",
    "platform_registry_version",
    "tier",
]

_DEFAULT_AUDIT_SOURCE: AuditSource = "api"


def set_default_audit_source(source: AuditSource) -> None:
    global _DEFAULT_AUDIT_SOURCE
    _DEFAULT_AUDIT_SOURCE = source


def get_default_audit_source() -> AuditSource:
    return _DEFAULT_AUDIT_SOURCE


class AuditEvent(BaseModel):
    id: uuid.UUID = Field(default_factory=uuid.uuid4)
    version: Literal[1] = 1
    source: AuditSource = Field(default_factory=get_default_audit_source)
    organization_id: uuid.UUID | None = None
    """Organization ID. None for platform-level operations (superuser without org context)."""
    workspace_id: uuid.UUID | None = None
    """Workspace ID. None for platform/org-level operations."""
    actor_type: AuditEventActor
    actor_id: uuid.UUID
    actor_label: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    request_id: str | None = None
    resource_type: AuditResourceType
    resource_id: str | None = None
    parent_resource_type: AuditResourceType | None = None
    parent_resource_id: str | None = None
    action: AuditAction
    status: AuditEventStatus
    data: dict[str, Any] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

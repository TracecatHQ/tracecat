from __future__ import annotations

import uuid
from collections.abc import Mapping
from datetime import UTC, datetime
from typing import Literal

from pydantic import BaseModel, Field

from tracecat.audit.enums import AuditEventActor, AuditEventStatus

AuditSink = Literal["organization", "platform"]
type AuditMetadataValue = str | bool | int | None | list[str]
type AuditMetadata = Mapping[str, AuditMetadataValue]
AuditAction = Literal[
    "create",
    "update",
    "upsert",
    "delete",
    "publish",
    "cancel",
    "terminate",
    "reset",
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
    "case_trigger",
    "webhook",
    "webhook_api_key",
    "workspace_variable",
    "tag",
    "table",
    "table_column",
    "organization_setting",
    "secret",
    "organization_secret",
    "case",
    "case_comment",
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


class AuditEvent(BaseModel):
    """A privacy-bounded record of an actor acting on a resource.

    ``actor_label``, ``ip_address``, and ``user_agent`` are intentionally
    modeled separately from generic metadata because they contain PII or
    sensitive security context. Stable actor and resource IDs are the primary
    attribution fields.
    """

    organization_id: uuid.UUID | None = None
    workspace_id: uuid.UUID | None = None
    actor_type: AuditEventActor
    actor_id: uuid.UUID
    actor_label: str | None = None
    ip_address: str | None = None
    user_agent: str | None = None
    resource_type: AuditResourceType
    resource_id: uuid.UUID | None = None
    action: AuditAction
    status: AuditEventStatus
    data: dict[str, AuditMetadataValue] | None = None
    created_at: datetime = Field(default_factory=lambda: datetime.now(UTC))

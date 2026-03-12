from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from tracecat.core.schemas import Schema
from tracecat.identifiers import OrganizationID, UserID, WorkspaceID


class ServiceAccountScopeRead(Schema):
    id: UUID
    name: str
    resource: str
    action: str
    description: str | None = None


class ServiceAccountApiKeyRead(Schema):
    id: UUID
    name: str
    key_id: str
    preview: str
    created_by: UserID | None = None
    revoked_by: UserID | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ServiceAccountRead(Schema):
    id: UUID
    organization_id: OrganizationID
    workspace_id: WorkspaceID | None = None
    owner_user_id: UserID | None = None
    name: str
    description: str | None = None
    disabled_at: datetime | None = None
    last_used_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    scopes: list[ServiceAccountScopeRead] = Field(default_factory=list)
    api_key: ServiceAccountApiKeyRead | None = None


class ServiceAccountCreate(Schema):
    name: str = Field(default=..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=512)
    scope_ids: list[UUID] = Field(default_factory=list)
    initial_key_name: str = Field(default="Primary", min_length=1, max_length=255)


class ServiceAccountUpdate(Schema):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=512)
    scope_ids: list[UUID] | None = None


class ServiceAccountApiKeyCreate(Schema):
    name: str = Field(default="Primary", min_length=1, max_length=255)


class ServiceAccountCreateResponse(Schema):
    api_key: str
    service_account: ServiceAccountRead


class ServiceAccountScopeList(Schema):
    items: list[ServiceAccountScopeRead] = Field(default_factory=list)

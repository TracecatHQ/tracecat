from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field

from tracecat.core.schemas import Schema
from tracecat.identifiers import OrganizationID, UserID, WorkspaceID


class ApiKeyScopeRead(Schema):
    id: UUID
    name: str
    resource: str
    action: str
    description: str | None = None


class ApiKeyRead(Schema):
    id: UUID
    name: str
    description: str | None = None
    key_id: str
    preview: str
    created_by: UserID | None = None
    revoked_by: UserID | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime
    scopes: list[ApiKeyScopeRead] = Field(default_factory=list)


class OrganizationApiKeyRead(ApiKeyRead):
    organization_id: OrganizationID


class WorkspaceApiKeyRead(ApiKeyRead):
    workspace_id: WorkspaceID


class ApiKeyCreate(Schema):
    name: str = Field(default=..., min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=512)
    scope_ids: list[UUID] = Field(default_factory=list)


class ApiKeyUpdate(Schema):
    name: str | None = Field(default=None, min_length=1, max_length=255)
    description: str | None = Field(default=None, max_length=512)
    scope_ids: list[UUID] | None = None


class OrganizationApiKeyCreateResponse(Schema):
    api_key: str
    key: OrganizationApiKeyRead


class WorkspaceApiKeyCreateResponse(Schema):
    api_key: str
    key: WorkspaceApiKeyRead


class ApiKeyScopeList(Schema):
    items: list[ApiKeyScopeRead] = Field(default_factory=list)

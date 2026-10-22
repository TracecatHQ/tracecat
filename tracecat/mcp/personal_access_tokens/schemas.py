"""Schemas for MCP personal access token management."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import Field, field_validator

from tracecat.core.schemas import Schema
from tracecat.identifiers import OrganizationID, UserID, WorkspaceID


class MCPPersonalAccessTokenRead(Schema):
    id: UUID
    user_id: UserID
    organization_id: OrganizationID
    workspace_id: WorkspaceID
    name: str
    key_id: str
    preview: str
    expires_at: datetime | None = None
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_by: UserID | None = None
    revoked_by: UserID | None = None
    created_at: datetime
    updated_at: datetime


class MCPPersonalAccessTokenCreate(Schema):
    name: str = Field(default=..., min_length=1, max_length=255)
    expires_at: datetime | None = None

    @field_validator("name")
    @classmethod
    def _check_name(cls, value: str) -> str:
        if "\x00" in value:
            raise ValueError("Name cannot contain NUL characters")
        return value


class IssuedMCPPersonalAccessToken(Schema):
    raw_token: str
    token: MCPPersonalAccessTokenRead


class MCPPersonalAccessTokenIssueResponse(Schema):
    issued_token: IssuedMCPPersonalAccessToken

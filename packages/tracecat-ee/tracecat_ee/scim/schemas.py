"""API schemas for SCIM connection credentials (EE)."""

from __future__ import annotations

from datetime import datetime
from uuid import UUID

from pydantic import BaseModel

from tracecat.core.schemas import Schema


class ScimConnectionRead(Schema):
    """Status of an organization's SCIM connection. Never carries the token."""

    id: UUID
    organization_id: UUID
    preview: str
    last_used_at: datetime | None = None
    revoked_at: datetime | None = None
    created_at: datetime
    updated_at: datetime


class ScimConnectionTokenRead(BaseModel):
    """A freshly issued token. The raw value is returned exactly once."""

    connection: ScimConnectionRead
    token: str

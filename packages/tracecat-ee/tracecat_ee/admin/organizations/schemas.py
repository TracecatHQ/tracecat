"""Organization management schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from tracecat.core.schemas import Schema


class OrgCreate(Schema):
    """Create organization request."""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=63, pattern=r"^[a-z0-9-]+$")


class OrgUpdate(Schema):
    """Update organization request."""

    name: str | None = None
    slug: str | None = None
    is_active: bool | None = None


class OrgRead(Schema):
    """Organization response."""

    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}

"""Organization management schemas."""

from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import Field

from tracecat.core.schemas import Schema
from tracecat.ee.compute.schemas import Tier


class OrgCreate(Schema):
    """Create organization request."""

    name: str = Field(..., min_length=1, max_length=255)
    slug: str = Field(..., min_length=1, max_length=63, pattern=r"^[a-z0-9-]+$")
    tier: Tier = Field(default=Tier.STARTER)


class OrgUpdate(Schema):
    """Update organization request."""

    name: str | None = Field(None, min_length=1, max_length=255)
    slug: str | None = Field(None, min_length=1, max_length=63, pattern=r"^[a-z0-9-]+$")
    is_active: bool | None = None
    tier: Tier | None = None


class OrgUpdateTier(Schema):
    """Update organization tier request."""

    tier: Tier


class OrgRead(Schema):
    """Organization response."""

    id: uuid.UUID
    name: str
    slug: str
    is_active: bool
    tier: Tier
    created_at: datetime
    updated_at: datetime | None = None

    model_config = {"from_attributes": True}

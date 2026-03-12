"""Platform agent admin schemas."""

from __future__ import annotations

from datetime import datetime
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field

from tracecat.agent.types import ModelDiscoveryStatus


class PlatformCatalogEntry(BaseModel):
    id: UUID
    model_provider: str = Field(..., min_length=1, max_length=120)
    model_name: str = Field(..., min_length=1, max_length=500)
    metadata: dict[str, Any] | None = None


class PlatformCatalogRead(BaseModel):
    discovery_status: ModelDiscoveryStatus
    last_refreshed_at: datetime | None = None
    last_error: str | None = None
    next_cursor: str | None = None
    models: list[PlatformCatalogEntry] = Field(default_factory=list)

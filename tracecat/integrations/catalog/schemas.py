"""Pydantic schemas for the consolidated integrations API."""

from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from pydantic import BaseModel, ConfigDict, Field

from tracecat.integrations.enums import (
    ConnectionAuthMethod,
    IntegrationSource,
    IntegrationStatus,
    OAuthGrantType,
)
from tracecat.secrets.constants import DEFAULT_SECRETS_ENVIRONMENT


class CatalogCredentialField(BaseModel):
    """Credential field required by a static/auth configuration option."""

    key: str
    label: str
    required: bool = True
    secret: bool = True
    multiline: bool = False
    placeholder: str | None = None
    description: str | None = None


class CatalogAuthOption(BaseModel):
    """Supported authentication path for a catalog integration."""

    auth_method: ConnectionAuthMethod
    label: str
    description: str | None = None
    provider_id: str | None = None
    grant_type: OAuthGrantType | None = None
    requires_config: bool = False
    enabled: bool = True
    status: IntegrationStatus | None = None
    fields: list[CatalogCredentialField] = Field(default_factory=list)


class CatalogIntegrationRead(BaseModel):
    """Catalog row for an integration."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    workspace_id: uuid.UUID | None
    namespace: str
    display_name: str
    description: str | None = None
    icon_url: str | None = None
    source: IntegrationSource
    auth_options: list[CatalogAuthOption] = Field(default_factory=list)
    created_at: datetime
    updated_at: datetime


class CatalogConnectionRead(BaseModel):
    """A user/workspace authenticated binding to an integration."""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    integration_id: uuid.UUID
    workspace_id: uuid.UUID
    user_id: uuid.UUID | None
    auth_method: ConnectionAuthMethod
    label: str
    expires_at: datetime | None
    scope: str | None
    metadata: dict[str, Any] = Field(default_factory=dict, alias="metadata_")
    created_at: datetime
    updated_at: datetime
    is_expired: bool = False


class CatalogIntegrationDetail(CatalogIntegrationRead):
    """Integration row enriched with related connections."""

    connections: list[CatalogConnectionRead] = Field(default_factory=list)


class CatalogStaticKVConnectionCreate(BaseModel):
    """Connection backed by an arbitrary key-value blob."""

    auth_method: ConnectionAuthMethod = ConnectionAuthMethod.STATIC_KV
    environment: str = DEFAULT_SECRETS_ENVIRONMENT
    keys: dict[str, str]


CatalogConnectionCreate = CatalogStaticKVConnectionCreate

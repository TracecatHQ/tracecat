"""Types for the platform MCP catalog."""

from __future__ import annotations

import uuid
from dataclasses import dataclass
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field

from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.schemas import (
    MCPConnectionOption,
    MCPConnectionSpec,
    MCPConnectionTarget,
    PlatformMCPCatalogStatus,
)


@dataclass(slots=True)
class PlatformMCPCatalogEntry:
    """Runtime platform MCP catalog row.

    Internal, already-validated data assembled from the trust-boundary
    ``Raw*`` models. Display metadata is safe to ship in OSS. Connect recipes
    are populated only from the private catalog overlay.
    """

    id: uuid.UUID
    slug: str
    name: str
    description: str
    category: str
    status: PlatformMCPCatalogStatus
    sort_key: str
    icon_url: str | None = None
    docs_url: str | None = None
    provider_id: str | None = None
    connection_spec: MCPConnectionSpec | None = None
    connection_options: list[MCPConnectionOption] | None = None


class RawCredential(BaseModel):
    """Credential row from bundled catalog JSON.

    ``target`` is required: the catalog is repo-owned, so every credential
    states explicitly where its value is routed at connect time instead of
    relying on runtime inference.
    """

    model_config = ConfigDict(extra="ignore")

    key: str = Field(min_length=1)
    label: str | None = None
    description: str | None = None
    required: bool = True
    secret: bool = True
    target: MCPConnectionTarget


class RawPackageOption(BaseModel):
    """stdio package row from bundled catalog JSON."""

    model_config = ConfigDict(extra="ignore")

    command: str = Field(min_length=1)
    manager: str | None = None
    args: list[str] = Field(default_factory=list)
    package: str | None = None


class RawHttpConnectionSpec(BaseModel):
    """HTTP connection spec from bundled catalog JSON.

    ``auth_type`` is nullable because coming-soon rows ship without a connect
    recipe; the loader maps a null ``auth_type`` to "no connection spec".
    """

    model_config = ConfigDict(extra="ignore")

    server_type: Literal["http"]
    auth_type: MCPAuthType | None = None
    server_uri: str | None = None
    credentials: list[RawCredential] | None = None
    scopes: list[str] | None = None
    oauth_authorization_endpoint: str | None = None
    oauth_token_endpoint: str | None = None


class RawStdioConnectionSpec(BaseModel):
    """stdio connection spec from bundled catalog JSON.

    ``auth_type`` is nullable because coming-soon rows ship without a connect
    recipe; the loader maps a null ``auth_type`` to "no connection spec".
    OAuth is excluded: MCP OAuth is defined for HTTP transports only, so a
    stdio spec claiming OAuth fails validation and drops the row.
    """

    model_config = ConfigDict(extra="ignore")

    server_type: Literal["stdio"]
    auth_type: Literal[MCPAuthType.CUSTOM, MCPAuthType.NONE] | None = None
    credentials: list[RawCredential] | None = None
    stdio_command: str | None = None
    stdio_args: list[str] | None = None
    stdio_env: list[str] | None = None
    packages: list[RawPackageOption] | None = None


RawConnectionSpec = Annotated[
    RawHttpConnectionSpec | RawStdioConnectionSpec,
    Field(discriminator="server_type"),
]
"""Connection spec discriminated on ``server_type``.

Each variant only declares the fields valid for its transport, so cross-type
nulls in the bundled JSON (e.g. ``stdio_args: null`` on an http row) are
dropped by ``extra="ignore"`` instead of failing validation.
"""


class RawConnectionOption(BaseModel):
    """Connect option row from bundled catalog JSON."""

    model_config = ConfigDict(extra="ignore")

    id: str | None = None
    label: str | None = None
    description: str | None = None
    docs: str | None = None
    connection_spec: RawConnectionSpec | None = None


class RawCatalogRow(BaseModel):
    """One catalog server entry from bundled JSON.

    Validates recursively in a single ``model_validate``; a malformed row (or
    any malformed nested spec/option) raises ``ValidationError`` so the loader
    skips the whole row rather than shipping a half-built entry.
    """

    model_config = ConfigDict(extra="ignore")

    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    category: str = Field(min_length=1)
    status: PlatformMCPCatalogStatus | None = None
    icon: str | None = None
    docs: str | None = None
    provider_id: str | None = None
    connection_spec: RawConnectionSpec | None = None
    connection_options: list[RawConnectionOption] | None = None
    default_connection_option: str | None = None

    @property
    def has_connection_metadata(self) -> bool:
        return bool(self.connection_spec or self.connection_options)

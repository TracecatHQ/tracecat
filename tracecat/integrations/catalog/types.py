"""Types for the platform MCP catalog."""

from __future__ import annotations

import uuid
from typing import Any, NotRequired, TypedDict

from pydantic import BaseModel, ConfigDict, Field, field_validator

from tracecat.integrations.enums import MCPAuthType
from tracecat.integrations.schemas import (
    MCPConnectionOption,
    MCPConnectionSpec,
    PlatformMCPCatalogStatus,
)
from tracecat.integrations.types import MCPServerType


class PlatformMCPCatalogEntry(TypedDict):
    """Runtime platform MCP catalog row.

    Display metadata is safe to ship in OSS. Connect recipes are populated only
    from the private catalog overlay.
    """

    id: uuid.UUID
    slug: str
    name: str
    description: str
    category: str
    status: PlatformMCPCatalogStatus
    icon_url: NotRequired[str | None]
    docs_url: NotRequired[str | None]
    provider_id: NotRequired[str | None]
    connection_spec: NotRequired[MCPConnectionSpec | None]
    connection_options: NotRequired[list[MCPConnectionOption] | None]
    sort_key: str


class RawCatalogRow(BaseModel):
    """One catalog server entry from bundled JSON."""

    model_config = ConfigDict(extra="ignore")

    slug: str = Field(min_length=1)
    name: str = Field(min_length=1)
    description: str = Field(min_length=1)
    category: str = Field(min_length=1)
    status: PlatformMCPCatalogStatus | None = None
    icon: str | None = None
    docs: str | None = None
    provider_id: str | None = None
    connection_spec: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None
    connection_options: list[dict[str, Any]] | None = None
    options: list[dict[str, Any]] | None = None
    default_connection_option: str | None = None
    default_option: str | None = None

    @field_validator("status", mode="before")
    @classmethod
    def _drop_unknown_status(cls, value: object) -> object:
        """Unknown status falls back to coming_soon downstream."""
        if value in {"available", "coming_soon", "deprecated", "hidden"}:
            return value
        return None

    @field_validator(
        "icon",
        "docs",
        "provider_id",
        "default_connection_option",
        "default_option",
        mode="before",
    )
    @classmethod
    def _optional_string(cls, value: object) -> str | None:
        return value if isinstance(value, str) else None

    @field_validator("connection_spec", "metadata", mode="before")
    @classmethod
    def _optional_dict(cls, value: object) -> dict[str, Any] | None:
        if not isinstance(value, dict):
            return None
        return {str(key): item for key, item in value.items()}

    @field_validator("connection_options", "options", mode="before")
    @classmethod
    def _optional_dict_list(cls, value: object) -> list[dict[str, Any]] | None:
        if not isinstance(value, list):
            return None
        rows: list[dict[str, Any]] = []
        for item in value:
            if isinstance(item, dict):
                rows.append({str(key): field for key, field in item.items()})
        return rows

    @property
    def has_connection_metadata(self) -> bool:
        return bool(
            self.connection_spec
            or self.metadata
            or self.connection_options
            or self.options
        )


class RawCredential(BaseModel):
    """Untrusted credential row from catalog JSON."""

    model_config = ConfigDict(extra="ignore")

    key: str = Field(min_length=1)
    label: str | None = None
    description: str | None = None
    required: bool = True
    secret: bool = True
    target: str | None = None


class RawPackageOption(BaseModel):
    """Untrusted stdio package row from catalog JSON."""

    model_config = ConfigDict(extra="ignore")

    command: str = Field(min_length=1)
    manager: str | None = None
    args: list[str] = Field(default_factory=list)
    package: str | None = None


class RawConnectionSpec(BaseModel):
    """Untrusted catalog connection spec JSON."""

    model_config = ConfigDict(extra="ignore")

    server_type: MCPServerType
    auth_type: MCPAuthType
    server_uri: str | None = None
    credentials: list[Any] = Field(default_factory=list)
    scopes: list[str] = Field(default_factory=list)
    oauth_authorization_endpoint: str | None = None
    oauth_token_endpoint: str | None = None
    stdio_command: str | None = None
    stdio_args: list[str] = Field(default_factory=list)
    stdio_env: list[str] = Field(default_factory=list)
    packages: list[Any] = Field(default_factory=list)


class RawConnectionOption(BaseModel):
    """Untrusted connect option row from catalog JSON."""

    model_config = ConfigDict(extra="ignore")

    id: Any = None
    label: Any = None
    description: Any = None
    docs: Any = None
    docs_url: Any = None
    connection_spec: dict[str, Any] | None = None
    metadata: dict[str, Any] | None = None

"""Schemas for custom LLM provider management."""

from datetime import datetime
from urllib.parse import urlparse
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator


def validate_base_url(value: str | None) -> str | None:
    """Validate a base_url is http(s) and has a hostname."""
    if value is None:
        return None
    parsed = urlparse(value)
    if not parsed.netloc:
        raise ValueError("base_url must include a hostname")
    if parsed.scheme.lower() not in ("http", "https"):
        raise ValueError("base_url must use HTTP or HTTPS")
    return value


class AgentCustomProviderCreate(BaseModel):
    """Create custom LLM provider."""

    display_name: str = Field(..., max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    passthrough: bool = Field(default=False)
    api_key_header: str | None = Field(default=None, max_length=120)
    api_key: str | None = Field(default=None)
    custom_headers: dict[str, str] | None = Field(default=None)
    allowed_tools: list[str] | None = Field(default=None)
    """Optional whitelist of Claude SDK built-in tools the runtime is
    allowed to enable for ``ai.action`` invocations using this source.
    ``None`` keeps the SDK default (full toolset). An empty list ``[]``
    disables all built-in tools — useful for upstream backends that do
    not have access to the SDK's host environment (Bash, Read, Edit,
    etc. cannot run on a remote LLM endpoint anyway). A specific list
    such as ``["Read", "Grep"]`` whitelists those tool names only."""

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        return validate_base_url(value)


class AgentCustomProviderRead(BaseModel):
    """Read custom provider."""

    model_config = ConfigDict(from_attributes=True)

    id: UUID
    organization_id: UUID
    display_name: str
    base_url: str | None
    passthrough: bool
    api_key_header: str | None
    last_refreshed_at: datetime | None
    allowed_tools: list[str] | None = None


class AgentCustomProviderUpdate(BaseModel):
    """Update custom provider."""

    display_name: str | None = Field(default=None, max_length=200)
    base_url: str | None = Field(default=None, max_length=500)
    passthrough: bool | None = None
    api_key_header: str | None = Field(default=None, max_length=120)
    api_key: str | None = None
    custom_headers: dict[str, str] | None = None
    allowed_tools: list[str] | None = None

    @field_validator("base_url")
    @classmethod
    def _validate_base_url(cls, value: str | None) -> str | None:
        return validate_base_url(value)


class AgentCustomProviderListResponse(BaseModel):
    """List response with pagination."""

    items: list[AgentCustomProviderRead]
    next_cursor: str | None = None

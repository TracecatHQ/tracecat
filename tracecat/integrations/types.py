from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, SecretStr, field_validator

type MCPServerType = Literal["http", "stdio"]


class OAuthServerMetadata(BaseModel):
    """Authorization-server / protected-resource metadata (untrusted RFC 8414/9728)."""

    model_config = ConfigDict(extra="ignore")

    resource: str | None = None
    authorization_servers: list[str] = Field(default_factory=list)
    authorization_endpoint: str | None = None
    token_endpoint: str | None = None
    registration_endpoint: str | None = None
    token_endpoint_auth_methods_supported: list[str] = Field(default_factory=list)

    @field_validator(
        "authorization_servers",
        "token_endpoint_auth_methods_supported",
        mode="before",
    )
    @classmethod
    def _only_strings(cls, value: object) -> list[str]:
        """Drop malformed list items rather than rejecting the metadata document."""
        return (
            [item for item in value if isinstance(item, str)]
            if isinstance(value, list)
            else []
        )

    @field_validator(
        "resource",
        "authorization_endpoint",
        "token_endpoint",
        "registration_endpoint",
        mode="before",
    )
    @classmethod
    def _optional_string(cls, value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @property
    def is_complete(self) -> bool:
        return bool(self.authorization_endpoint and self.token_endpoint)

    @classmethod
    def from_json(cls, value: object) -> OAuthServerMetadata | None:
        if not isinstance(value, dict):
            return None
        return cls.model_validate(value)


class DCRResponse(BaseModel):
    """Dynamic client registration response (untrusted RFC 7591 JSON)."""

    model_config = ConfigDict(extra="ignore")

    client_id: str = Field(min_length=1)
    client_secret: str | None = None
    token_endpoint_auth_method: str | None = None

    @field_validator("client_id")
    @classmethod
    def _non_empty_client_id(cls, value: str) -> str:
        client_id = value.strip()
        if not client_id:
            raise ValueError("Dynamic client registration did not return client_id")
        return client_id

    @field_validator("client_secret", "token_endpoint_auth_method", mode="before")
    @classmethod
    def _optional_string(cls, value: object) -> str | None:
        return value if isinstance(value, str) and value else None


class _OAuthTokenWireResponse(BaseModel):
    """OAuth token endpoint response JSON."""

    model_config = ConfigDict(extra="ignore")

    access_token: str = Field(min_length=1)
    refresh_token: str | None = None
    expires_in: int | None = None
    scope: str | None = None
    token_type: str | None = None

    @field_validator("access_token")
    @classmethod
    def _non_empty_access_token(cls, value: str) -> str:
        access_token = value.strip()
        if not access_token:
            raise ValueError("OAuth token response did not include access_token")
        return access_token

    @field_validator("refresh_token", "scope", "token_type", mode="before")
    @classmethod
    def _optional_string(cls, value: object) -> str | None:
        return value if isinstance(value, str) and value else None

    @field_validator("expires_in", mode="before")
    @classmethod
    def _optional_int(cls, value: object) -> int | None:
        return value if isinstance(value, int) and not isinstance(value, bool) else None


@dataclass(slots=True)
class TokenResponse:
    """Data class for OAuth token responses."""

    access_token: SecretStr
    refresh_token: SecretStr | None = None
    expires_in: int | None = 3600
    scope: str = ""
    token_type: str = "Bearer"

    @classmethod
    def from_oauth_response(
        cls,
        value: object,
        *,
        default_refresh_token: str | None = None,
        default_expires_in: int | None = 3600,
        default_scope: str = "",
        default_token_type: str = "Bearer",
    ) -> TokenResponse:
        token = _OAuthTokenWireResponse.model_validate(value)
        refresh_token = token.refresh_token or default_refresh_token
        return cls(
            access_token=SecretStr(token.access_token),
            refresh_token=SecretStr(refresh_token) if refresh_token else None,
            expires_in=token.expires_in
            if token.expires_in is not None
            else default_expires_in,
            scope=token.scope or default_scope,
            token_type=token.token_type or default_token_type,
        )

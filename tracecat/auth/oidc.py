"""Platform OIDC/OAuth client configuration utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

from httpx_oauth.clients.google import GoogleOAuth2
from httpx_oauth.clients.openid import OpenID
from httpx_oauth.oauth2 import BaseOAuth2

from tracecat import config
from tracecat.auth.enums import AuthType

_DEFAULT_OIDC_SCOPES: Final[tuple[str, ...]] = ("openid", "profile", "email")


@dataclass(frozen=True)
class OIDCProviderConfig:
    """Normalized OIDC provider configuration."""

    issuer: str | None
    client_id: str
    client_secret: str
    scopes: tuple[str, ...]

    @property
    def discovery_endpoint(self) -> str | None:
        if self.issuer is None:
            return None
        return f"{self.issuer}/.well-known/openid-configuration"


def oidc_auth_type_enabled() -> bool:
    """Whether platform auth policy allows OIDC-style login."""
    return (
        AuthType.OIDC in config.TRACECAT__AUTH_TYPES
        or AuthType.GOOGLE_OAUTH in config.TRACECAT__AUTH_TYPES
    )


def get_platform_oidc_config() -> OIDCProviderConfig:
    """Return normalized OIDC config with backward-compatible defaults."""
    issuer = config.OIDC_ISSUER.strip().rstrip("/") or None
    scopes = config.OIDC_SCOPES or _DEFAULT_OIDC_SCOPES
    return OIDCProviderConfig(
        issuer=issuer,
        client_id=config.OIDC_CLIENT_ID,
        client_secret=config.OIDC_CLIENT_SECRET,
        scopes=scopes,
    )


def create_platform_oauth_client() -> BaseOAuth2:
    """Create the platform OAuth client used for login/callback routes.

    Behavior:
    - If `OIDC_ISSUER` is configured, use generic OIDC discovery.
    - Otherwise, fall back to Google OAuth client wiring for legacy deployments.
    """
    oidc_config = get_platform_oidc_config()
    scopes = list(oidc_config.scopes)
    if oidc_config.discovery_endpoint is not None:
        return OpenID(
            client_id=oidc_config.client_id,
            client_secret=oidc_config.client_secret,
            openid_configuration_endpoint=oidc_config.discovery_endpoint,
            name="oidc",
            base_scopes=scopes,
        )
    return GoogleOAuth2(
        client_id=oidc_config.client_id,
        client_secret=oidc_config.client_secret,
        scopes=scopes,
        name="oidc",
    )

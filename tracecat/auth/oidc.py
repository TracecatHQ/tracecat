"""Platform OIDC/OAuth client configuration utilities."""

from __future__ import annotations

from dataclasses import dataclass
from typing import Final

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
    """Whether platform auth policy both allows and configures OIDC login."""
    return AuthType.OIDC in config.TRACECAT__AUTH_TYPES and bool(
        config.OIDC_ISSUER.strip()
    )


def get_platform_oidc_config() -> OIDCProviderConfig:
    """Return normalized OIDC config."""
    issuer = config.OIDC_ISSUER.strip().rstrip("/") or None
    scopes = config.OIDC_SCOPES or _DEFAULT_OIDC_SCOPES
    return OIDCProviderConfig(
        issuer=issuer,
        client_id=config.OIDC_CLIENT_ID,
        client_secret=config.OIDC_CLIENT_SECRET,
        scopes=scopes,
    )


def create_platform_oauth_client() -> BaseOAuth2:
    """Create the platform OAuth client used for login/callback routes."""
    oidc_config = get_platform_oidc_config()
    scopes = list(oidc_config.scopes)
    discovery_endpoint = oidc_config.discovery_endpoint
    if discovery_endpoint is None:
        raise ValueError("OIDC_ISSUER must be configured for OIDC login.")
    return OpenID(
        client_id=oidc_config.client_id,
        client_secret=oidc_config.client_secret,
        openid_configuration_endpoint=discovery_endpoint,
        name="oidc",
        base_scopes=scopes,
    )

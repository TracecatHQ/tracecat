"""Shared helpers and base classes for Google OAuth providers.

This module intentionally holds no provider registrations so that it can be
imported from every Google provider module without cycles.
"""

from typing import Any, ClassVar

from tracecat.integrations.providers.base import AuthorizationCodeOAuthProvider
from tracecat.integrations.schemas import ProviderMetadata

GOOGLE_AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
GOOGLE_TOKEN_URL = "https://oauth2.googleapis.com/token"

GOOGLE_AC_SETUP_GUIDE_URL = (
    "https://developers.google.com/identity/protocols/oauth2/web-server"
)
GOOGLE_CC_SETUP_GUIDE_URL = (
    "https://developers.google.com/workspace/guides/create-credentials"
)
GOOGLE_TROUBLESHOOT_URL = "https://developers.google.com/workspace/support"


class GoogleAuthorizationCodeOAuthProvider(AuthorizationCodeOAuthProvider):
    """Base class for Google authorization-code OAuth providers."""

    default_authorization_endpoint: ClassVar[str | None] = GOOGLE_AUTH_URL
    default_token_endpoint: ClassVar[str | None] = GOOGLE_TOKEN_URL

    def _use_pkce(self) -> bool:
        """Enable PKCE for enhanced security (recommended by Google)."""
        return True

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Add Google-specific authorization parameters."""
        params = super()._get_additional_authorize_params()
        # Request offline access to get refresh token
        params["access_type"] = "offline"
        # Force consent screen to ensure we get a refresh token
        params["prompt"] = "consent"
        return params


def get_google_ac_metadata(
    id: str,
    name: str,
    *,
    api_docs_url: str,
    setup_guide_url: str = GOOGLE_AC_SETUP_GUIDE_URL,
    troubleshooting_url: str = GOOGLE_TROUBLESHOOT_URL,
    description: str | None = None,
) -> ProviderMetadata:
    """Build metadata for a Google authorization-code (user OAuth) provider."""
    return ProviderMetadata(
        id=id,
        name=f"{name} (User OAuth)",
        description=description
        or (
            f"Connect a Google account with OAuth to call the {name} API as that user."
        ),
        requires_config=True,
        enabled=True,
        api_docs_url=api_docs_url,
        setup_guide_url=setup_guide_url,
        troubleshooting_url=troubleshooting_url,
    )


def get_google_cc_metadata(
    id: str,
    name: str,
    *,
    api_docs_url: str,
    setup_guide_url: str = GOOGLE_CC_SETUP_GUIDE_URL,
    troubleshooting_url: str = GOOGLE_TROUBLESHOOT_URL,
    description: str | None = None,
) -> ProviderMetadata:
    """Build metadata for a Google service account provider."""
    return ProviderMetadata(
        id=id,
        name=f"{name} (Service account)",
        description=description
        or (
            f"Authenticate to the {name} API with a service account JSON key. "
            "Set a subject to use domain-wide delegation."
        ),
        requires_config=True,
        enabled=True,
        service_account_json=True,
        api_docs_url=api_docs_url,
        setup_guide_url=setup_guide_url,
        troubleshooting_url=troubleshooting_url,
    )

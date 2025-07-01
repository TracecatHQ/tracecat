"""Google OAuth integration using generic OAuth provider."""

from typing import ClassVar

from tracecat.integrations.base import AuthorizationCodeOAuthProvider
from tracecat.integrations.models import (
    ProviderCategory,
    ProviderMetadata,
    ProviderScopes,
)


class GoogleOAuthProvider(AuthorizationCodeOAuthProvider):
    """Google OAuth provider using generic OAuth implementation."""

    id: ClassVar[str] = "google"

    # Google OAuth endpoints
    _authorization_endpoint: ClassVar[str] = (
        "https://accounts.google.com/o/oauth2/v2.0/auth"
    )
    _token_endpoint: ClassVar[str] = "https://oauth2.googleapis.com/token"

    # Google OAuth scopes
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "openid",
            "email",
            "profile",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/drive.readonly",
        ]
    )

    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="google",
        name="Google",
        description="Google OAuth provider for Workspace and Gmail integration",
        categories=[ProviderCategory.AUTH],
        setup_steps=[
            "Create a project in Google Cloud Console",
            "Enable required APIs (Gmail, Drive, etc.)",
            "Configure OAuth consent screen",
            "Go to Credentials > Create Credentials > OAuth client ID",
            "Add the redirect URI shown above to 'Authorized redirect URIs'",
            "Copy Client ID and Client Secret",
            "Configure credentials in Tracecat",
        ],
        enabled=False,
    )

    def _get_additional_authorize_params(self) -> dict[str, str]:
        """Add Google-specific authorization parameters."""
        return {
            "access_type": "offline",  # Request refresh token
            "prompt": "consent",  # Force consent to get refresh token
        }

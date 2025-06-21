"""Google OAuth integration using generic OAuth provider."""

from typing import ClassVar

from tracecat.integrations.base import BaseOAuthProvider
from tracecat.integrations.models import ProviderCategory, ProviderMetadata


class GoogleOAuthProvider(BaseOAuthProvider):
    """Google OAuth provider using generic OAuth implementation."""

    id: ClassVar[str] = "google"

    # Google OAuth endpoints
    authorization_endpoint: ClassVar[str] = (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    token_endpoint: ClassVar[str] = "https://oauth2.googleapis.com/token"

    # Default Google scopes
    default_scopes: ClassVar[list[str]] = [
        "openid",
        "email",
        "profile",
        "https://www.googleapis.com/auth/gmail.send",
        "https://www.googleapis.com/auth/drive.readonly",
    ]

    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="google",
        name="Google",
        description="Google OAuth provider for Workspace and Gmail integration",
        categories=[ProviderCategory.AUTH],
        features=[
            "OAuth 2.0",
            "Google Workspace",
            "Gmail API",
            "Drive Integration",
        ],
        setup_steps=[
            "Create a project in Google Cloud Console",
            "Enable required APIs (Gmail, Drive, etc.)",
            "Configure OAuth consent screen",
            "Create OAuth 2.0 credentials",
            "Add client ID and secret",
            "Test the connection",
        ],
    )

    def _get_additional_authorize_params(self) -> dict[str, str]:
        """Add Google-specific authorization parameters."""
        return {
            "access_type": "offline",  # Request refresh token
            "prompt": "consent",  # Force consent to get refresh token
        }

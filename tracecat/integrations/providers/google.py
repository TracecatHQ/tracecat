"""Google OAuth integration using generic OAuth provider."""

from typing import ClassVar

from tracecat.integrations.base import BaseOauthProvider


class GoogleOAuthProvider(BaseOauthProvider):
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

    def _get_additional_authorize_params(self) -> dict[str, str]:
        """Add Google-specific authorization parameters."""
        return {
            "access_type": "offline",  # Request refresh token
            "prompt": "consent",  # Force consent to get refresh token
        }

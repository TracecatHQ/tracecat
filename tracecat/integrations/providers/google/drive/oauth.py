"""Google Drive OAuth provider using authorization code flow.

This provider enables secure Google Drive API access through OAuth 2.0 authorization code flow.
Users can connect their Google account through Tracecat's integration UI.
"""

from typing import Any, ClassVar, Optional

from tracecat.integrations.providers.base import AuthorizationCodeOAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class GoogleDriveACProvider(AuthorizationCodeOAuthProvider):
    """Google Drive OAuth provider using authorization code flow for user access.
    
    This provider enables Drive API access for security automation workflows including:
    - File and folder management
    - Permission auditing and enforcement
    - Data loss prevention
    - Compliance automation
    """

    id: ClassVar[str] = "google_drive"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "https://www.googleapis.com/auth/drive.readonly",
        ],
        additional=[
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive.metadata",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="google_drive",
        name="Google Drive",
        description="Google Drive API access for file management, permissions, and security automation",
        setup_steps=[
            "Create a Google Cloud project at console.cloud.google.com",
            "Enable the Google Drive API in APIs & Services → Library",
            "Configure OAuth consent screen (External or Internal)",
            "Create OAuth 2.0 credentials in APIs & Services → Credentials",
            "Add authorized redirect URI shown below",
            "Copy Client ID and Client Secret to Tracecat",
            "Click 'Connect with OAuth' to authorize",
        ],
        requires_config=True,
        enabled=True,
        api_docs_url="https://developers.google.com/drive/api/reference/rest/v3",
        setup_guide_url="https://developers.google.com/drive/api/quickstart/python",
        troubleshooting_url="https://developers.google.com/drive/api/guides/handle-errors",
    )
    
    # Google OAuth endpoints
    default_authorization_endpoint: ClassVar[str] = (
        "https://accounts.google.com/o/oauth2/v2/auth"
    )
    default_token_endpoint: ClassVar[str] = (
        "https://oauth2.googleapis.com/token"
    )

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


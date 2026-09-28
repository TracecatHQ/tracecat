"""Google Drive OAuth providers."""

from typing import ClassVar

from tracecat.integrations.providers.google.common import (
    GoogleAuthorizationCodeOAuthProvider,
    get_google_ac_metadata,
    get_google_cc_metadata,
)
from tracecat.integrations.providers.google.service_account import (
    GoogleServiceAccountOAuthProvider,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

DRIVE_API_DOCS_URL = (
    "https://developers.google.com/workspace/drive/api/reference/rest/v3"
)
DRIVE_TROUBLESHOOT_URL = "https://developers.google.com/drive/api/guides/handle-errors"


class GoogleDriveACProvider(GoogleAuthorizationCodeOAuthProvider):
    """Google Drive provider using the authorization code flow for user access."""

    id: ClassVar[str] = "google_drive"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "https://www.googleapis.com/auth/drive.file",
            "https://www.googleapis.com/auth/drive.metadata",
            "https://www.googleapis.com/auth/drive.readonly",
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/drive.activity.readonly",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_google_ac_metadata(
        id="google_drive",
        name="Google Drive",
        api_docs_url=DRIVE_API_DOCS_URL,
        troubleshooting_url=DRIVE_TROUBLESHOOT_URL,
    )


class GoogleDriveCCProvider(GoogleServiceAccountOAuthProvider):
    """Google Drive provider using service account credentials."""

    id: ClassVar[str] = "google_drive"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "https://www.googleapis.com/auth/drive",
            "https://www.googleapis.com/auth/drive.activity.readonly",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_google_cc_metadata(
        id="google_drive",
        name="Google Drive",
        api_docs_url=DRIVE_API_DOCS_URL,
        troubleshooting_url=DRIVE_TROUBLESHOOT_URL,
    )

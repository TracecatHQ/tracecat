"""Google Sheets OAuth providers."""

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

SHEETS_API_DOCS_URL = (
    "https://developers.google.com/workspace/sheets/api/reference/rest"
)
SHEETS_TROUBLESHOOT_URL = "https://developers.google.com/sheets/api/troubleshooting"

GOOGLE_SHEETS_AC_SCOPES = ["https://www.googleapis.com/auth/spreadsheets"]
GOOGLE_SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]


class GoogleSheetsACProvider(GoogleAuthorizationCodeOAuthProvider):
    """Google Sheets provider using the authorization code flow for user access."""

    id: ClassVar[str] = "google_sheets"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=GOOGLE_SHEETS_AC_SCOPES)
    metadata: ClassVar[ProviderMetadata] = get_google_ac_metadata(
        id="google_sheets",
        name="Google Sheets",
        api_docs_url=SHEETS_API_DOCS_URL,
        troubleshooting_url=SHEETS_TROUBLESHOOT_URL,
    )


class GoogleSheetsOAuthProvider(GoogleServiceAccountOAuthProvider):
    """Google Sheets provider using service account credentials."""

    id: ClassVar[str] = "google_sheets"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=GOOGLE_SHEETS_SCOPES)
    metadata: ClassVar[ProviderMetadata] = get_google_cc_metadata(
        id="google_sheets",
        name="Google Sheets",
        api_docs_url=SHEETS_API_DOCS_URL,
        troubleshooting_url=SHEETS_TROUBLESHOOT_URL,
    )

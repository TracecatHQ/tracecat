"""Google Sheets OAuth provider. Inherits from GoogleServiceAccountOAuthProvider."""

from typing import ClassVar

from tracecat.integrations.providers.google.service_account import (
    GOOGLE_API_SETUP_STEPS,
    GoogleServiceAccountOAuthProvider,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

GOOGLE_SHEETS_SCOPES = [
    "https://www.googleapis.com/auth/spreadsheets",
    "https://www.googleapis.com/auth/spreadsheets.readonly",
]


class GoogleSheetsOAuthProvider(GoogleServiceAccountOAuthProvider):
    """Google Sheets OAuth provider using service account credentials."""

    id: ClassVar[str] = "google_sheets"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=GOOGLE_SHEETS_SCOPES)
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="google_sheets",
        name="Google Sheets (Service account)",
        description=(
            "Authenticate to Google Sheets API using a service account JSON key."
        ),
        requires_config=True,
        enabled=True,
        setup_steps=GOOGLE_API_SETUP_STEPS,
        api_docs_url="https://developers.google.com/workspace/sheets/api/reference/rest",
        setup_guide_url="https://developers.google.com/identity/protocols/oauth2/service-account",
        troubleshooting_url="https://developers.google.com/sheets/api/troubleshooting",
    )

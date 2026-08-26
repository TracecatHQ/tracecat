"""Google Gmail OAuth providers."""

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

GMAIL_API_DOCS_URL = "https://developers.google.com/workspace/gmail/api/reference/rest"
GMAIL_TROUBLESHOOT_URL = (
    "https://developers.google.com/identity/protocols/oauth2/web-server#httprest"
)


class GoogleGmailACProvider(GoogleAuthorizationCodeOAuthProvider):
    """Gmail provider using the authorization code flow for user access."""

    id: ClassVar[str] = "google_gmail"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "https://www.googleapis.com/auth/gmail.labels",
            "https://www.googleapis.com/auth/gmail.metadata",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.compose",
            "https://mail.google.com/",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_google_ac_metadata(
        id="google_gmail",
        name="Google Gmail",
        description=(
            "Connect a Google account with OAuth to call the Gmail API as that user."
        ),
        api_docs_url=GMAIL_API_DOCS_URL,
        troubleshooting_url=GMAIL_TROUBLESHOOT_URL,
    )


class GoogleGmailCCProvider(GoogleServiceAccountOAuthProvider):
    """Gmail provider using service account credentials."""

    id: ClassVar[str] = "google_gmail"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.compose",
            "https://mail.google.com/",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_google_cc_metadata(
        id="google_gmail",
        name="Google Gmail",
        description=(
            "Authenticate to the Gmail API with a service account JSON key. "
            "Set a subject to use domain-wide delegation."
        ),
        api_docs_url=GMAIL_API_DOCS_URL,
        troubleshooting_url=GMAIL_TROUBLESHOOT_URL,
    )

"""Google Docs OAuth providers."""

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

DOCS_API_DOCS_URL = "https://developers.google.com/workspace/docs/api/reference/rest"
DOCS_TROUBLESHOOT_URL = "https://developers.google.com/docs/api/troubleshooting"

GOOGLE_DOCS_AC_SCOPES = ["https://www.googleapis.com/auth/documents"]
GOOGLE_DOCS_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/documents.readonly",
]


class GoogleDocsACProvider(GoogleAuthorizationCodeOAuthProvider):
    """Google Docs provider using the authorization code flow for user access."""

    id: ClassVar[str] = "google_docs"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=GOOGLE_DOCS_AC_SCOPES)
    metadata: ClassVar[ProviderMetadata] = get_google_ac_metadata(
        id="google_docs",
        name="Google Docs",
        api_docs_url=DOCS_API_DOCS_URL,
        troubleshooting_url=DOCS_TROUBLESHOOT_URL,
    )


class GoogleDocsOAuthProvider(GoogleServiceAccountOAuthProvider):
    """Google Docs provider using service account credentials."""

    id: ClassVar[str] = "google_docs"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=GOOGLE_DOCS_SCOPES)
    metadata: ClassVar[ProviderMetadata] = get_google_cc_metadata(
        id="google_docs",
        name="Google Docs",
        api_docs_url=DOCS_API_DOCS_URL,
        troubleshooting_url=DOCS_TROUBLESHOOT_URL,
    )

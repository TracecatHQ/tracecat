"""Google Docs OAuth provider. Inherits from GoogleServiceAccountOAuthProvider."""

from typing import ClassVar

from tracecat.integrations.providers.google.service_account import (
    GOOGLE_API_SETUP_STEPS,
    GoogleServiceAccountOAuthProvider,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

GOOGLE_DOCS_SCOPES = [
    "https://www.googleapis.com/auth/documents",
    "https://www.googleapis.com/auth/documents.readonly",
]


class GoogleDocsOAuthProvider(GoogleServiceAccountOAuthProvider):
    """Google Docs OAuth provider using service account credentials."""

    id: ClassVar[str] = "google_docs"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=GOOGLE_DOCS_SCOPES)
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="google_docs",
        name="Google Docs (Service account)",
        description=(
            "Authenticate to Google Docs API using a service account JSON key."
        ),
        requires_config=True,
        enabled=True,
        setup_steps=GOOGLE_API_SETUP_STEPS,
        api_docs_url="https://developers.google.com/workspace/docs/api/reference/rest",
        setup_guide_url="https://developers.google.com/identity/protocols/oauth2/service-account",
        troubleshooting_url="https://developers.google.com/docs/api/troubleshooting",
    )

"""Google Chronicle OAuth providers."""

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

CHRONICLE_SCOPE = "https://www.googleapis.com/auth/chronicle"
CHRONICLE_SCOPES = [CHRONICLE_SCOPE]
CHRONICLE_API_DOCS_URL = "https://docs.cloud.google.com/chronicle/docs/reference/rest"
CHRONICLE_AUTH_DOCS_URL = (
    "https://docs.cloud.google.com/chronicle/docs/reference/authentication"
)


class GoogleChronicleACProvider(GoogleAuthorizationCodeOAuthProvider):
    """Google Chronicle provider using a user's authorization-code grant."""

    id: ClassVar[str] = "google_chronicle"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=CHRONICLE_SCOPES)
    metadata: ClassVar[ProviderMetadata] = get_google_ac_metadata(
        id="google_chronicle",
        name="Google Chronicle",
        description=(
            "Connect a Google account with access to a Google Security Operations "
            "instance and call the Chronicle API as that user."
        ),
        api_docs_url=CHRONICLE_API_DOCS_URL,
        setup_guide_url=CHRONICLE_AUTH_DOCS_URL,
        troubleshooting_url=CHRONICLE_AUTH_DOCS_URL,
    )


class GoogleChronicleCCProvider(GoogleServiceAccountOAuthProvider):
    """Google Chronicle provider using a service-account grant."""

    id: ClassVar[str] = "google_chronicle"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=CHRONICLE_SCOPES)
    metadata: ClassVar[ProviderMetadata] = get_google_cc_metadata(
        id="google_chronicle",
        name="Google Chronicle",
        description=(
            "Authenticate to the Google Chronicle API with a service account JSON "
            "key that has access to the target Google Security Operations instance."
        ),
        api_docs_url=CHRONICLE_API_DOCS_URL,
        setup_guide_url=CHRONICLE_AUTH_DOCS_URL,
        troubleshooting_url=CHRONICLE_AUTH_DOCS_URL,
    )

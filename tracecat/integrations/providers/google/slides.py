"""Google Slides OAuth providers."""

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

SLIDES_API_DOCS_URL = (
    "https://developers.google.com/workspace/slides/api/reference/rest"
)
SLIDES_TROUBLESHOOT_URL = "https://developers.google.com/workspace/slides/api/support"


class GoogleSlidesACProvider(GoogleAuthorizationCodeOAuthProvider):
    """Google Slides provider using the authorization code flow for user access."""

    id: ClassVar[str] = "google_slides"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["https://www.googleapis.com/auth/presentations"],
    )
    metadata: ClassVar[ProviderMetadata] = get_google_ac_metadata(
        id="google_slides",
        name="Google Slides",
        api_docs_url=SLIDES_API_DOCS_URL,
        troubleshooting_url=SLIDES_TROUBLESHOOT_URL,
    )


class GoogleSlidesCCProvider(GoogleServiceAccountOAuthProvider):
    """Google Slides provider using service account credentials."""

    id: ClassVar[str] = "google_slides"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "https://www.googleapis.com/auth/presentations",
            "https://www.googleapis.com/auth/presentations.readonly",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_google_cc_metadata(
        id="google_slides",
        name="Google Slides",
        api_docs_url=SLIDES_API_DOCS_URL,
        troubleshooting_url=SLIDES_TROUBLESHOOT_URL,
    )

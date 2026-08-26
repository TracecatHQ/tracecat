"""Google Forms OAuth providers."""

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

FORMS_API_DOCS_URL = "https://developers.google.com/workspace/forms/api/reference/rest"
FORMS_TROUBLESHOOT_URL = "https://developers.google.com/workspace/forms/api/support"


class GoogleFormsACProvider(GoogleAuthorizationCodeOAuthProvider):
    """Google Forms provider using the authorization code flow for user access."""

    id: ClassVar[str] = "google_forms"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "https://www.googleapis.com/auth/forms.body",
            "https://www.googleapis.com/auth/forms.responses.readonly",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_google_ac_metadata(
        id="google_forms",
        name="Google Forms",
        api_docs_url=FORMS_API_DOCS_URL,
        troubleshooting_url=FORMS_TROUBLESHOOT_URL,
    )


class GoogleFormsCCProvider(GoogleServiceAccountOAuthProvider):
    """Google Forms provider using service account credentials."""

    id: ClassVar[str] = "google_forms"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "https://www.googleapis.com/auth/forms.body",
            "https://www.googleapis.com/auth/forms.body.readonly",
            "https://www.googleapis.com/auth/forms.responses.readonly",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_google_cc_metadata(
        id="google_forms",
        name="Google Forms",
        api_docs_url=FORMS_API_DOCS_URL,
        troubleshooting_url=FORMS_TROUBLESHOOT_URL,
    )

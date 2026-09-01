"""Google Cloud Logging OAuth providers."""

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

CLOUD_LOGGING_SCOPE = "https://www.googleapis.com/auth/logging.read"
CLOUD_LOGGING_SCOPES = [CLOUD_LOGGING_SCOPE]
CLOUD_LOGGING_API_DOCS_URL = (
    "https://cloud.google.com/logging/docs/reference/v2/rest/v2/entries/list"
)
CLOUD_LOGGING_AC_SETUP_GUIDE_URL = (
    "https://developers.google.com/identity/protocols/oauth2/web-server"
)
CLOUD_LOGGING_CC_SETUP_GUIDE_URL = (
    "https://cloud.google.com/iam/docs/keys-create-delete"
)
CLOUD_LOGGING_TROUBLESHOOT_URL = "https://cloud.google.com/logging/docs/access-control"


class GoogleCloudLoggingACProvider(GoogleAuthorizationCodeOAuthProvider):
    """Google Cloud Logging provider using a user's authorization-code grant."""

    id: ClassVar[str] = "google_cloud_logging"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=CLOUD_LOGGING_SCOPES)
    metadata: ClassVar[ProviderMetadata] = get_google_ac_metadata(
        id="google_cloud_logging",
        name="Google Cloud Logging",
        description=(
            "Connect a Google account with access to Google Cloud Logging and "
            "read log entries as that user."
        ),
        api_docs_url=CLOUD_LOGGING_API_DOCS_URL,
        setup_guide_url=CLOUD_LOGGING_AC_SETUP_GUIDE_URL,
        troubleshooting_url=CLOUD_LOGGING_TROUBLESHOOT_URL,
    )


class GoogleCloudLoggingCCProvider(GoogleServiceAccountOAuthProvider):
    """Google Cloud Logging provider using a service-account grant."""

    id: ClassVar[str] = "google_cloud_logging"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=CLOUD_LOGGING_SCOPES)
    metadata: ClassVar[ProviderMetadata] = get_google_cc_metadata(
        id="google_cloud_logging",
        name="Google Cloud Logging",
        description=(
            "Authenticate to Google Cloud Logging with a service account JSON key "
            "that can read the target log entries."
        ),
        api_docs_url=CLOUD_LOGGING_API_DOCS_URL,
        setup_guide_url=CLOUD_LOGGING_CC_SETUP_GUIDE_URL,
        troubleshooting_url=CLOUD_LOGGING_TROUBLESHOOT_URL,
    )

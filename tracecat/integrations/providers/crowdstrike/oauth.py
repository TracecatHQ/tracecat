"""CrowdStrike OAuth provider using client credentials flow."""

from typing import ClassVar

from tracecat.integrations.providers.base import ClientCredentialsOAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

CROWDSTRIKE_API_DOCS_URL = "https://falcon.crowdstrike.com/documentation"
CROWDSTRIKE_TOKEN_DOCS_URL = "https://falcon.crowdstrike.com/documentation/page/a2a7fc0e/crowdstrike-oauth2-based-apis"

CROWDSTRIKE_TOKEN_ENDPOINT_HELP: list[str] = [
    "Select endpoint based on your Falcon cloud region:",
    "- US-1: https://api.crowdstrike.com/oauth2/token",
    "- US-2: https://api.us-2.crowdstrike.com/oauth2/token",
    "- EU-1: https://api.eu-1.crowdstrike.com/oauth2/token",
    "- US-GOV-1: https://api.laggar.gcw.crowdstrike.com/oauth2/token",
    "- US-GOV-2: https://api.us-gov-2.crowdstrike.mil/oauth2/token",
]


class CrowdStrikeCCProvider(ClientCredentialsOAuthProvider):
    """CrowdStrike Falcon OAuth provider using client credentials for API access.

    CrowdStrike uses role-based access control (RBAC) rather than OAuth scopes.
    API permissions are configured on the API client in the Falcon console.
    """

    id: ClassVar[str] = "crowdstrike"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="crowdstrike",
        name="CrowdStrike Falcon",
        description="CrowdStrike Falcon OAuth provider using client credentials for API access.",
        requires_config=True,
        enabled=True,
        api_docs_url=CROWDSTRIKE_API_DOCS_URL,
        setup_guide_url=CROWDSTRIKE_TOKEN_DOCS_URL,
        troubleshooting_url=CROWDSTRIKE_TOKEN_DOCS_URL,
    )
    # CrowdStrike client credentials flow doesn't use an authorization endpoint,
    # but the base class requires both. We use the token endpoint as a placeholder.
    default_authorization_endpoint: ClassVar[str | None] = (
        "https://api.crowdstrike.com/oauth2/token"
    )
    default_token_endpoint: ClassVar[str | None] = (
        "https://api.crowdstrike.com/oauth2/token"
    )
    token_endpoint_help: ClassVar[str | list[str] | None] = (
        CROWDSTRIKE_TOKEN_ENDPOINT_HELP
    )

"""Jamf Pro OAuth provider using client credentials flow."""

from typing import ClassVar

from tracecat.integrations.providers.base import ClientCredentialsOAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

JAMF_PRO_API_DOCS_URL = "https://developer.jamf.com/jamf-pro/reference/jamf-pro-api"
JAMF_PRO_SETUP_GUIDE_URL = "https://developer.jamf.com/jamf-pro/docs/client-credentials"


class JamfProOAuthProvider(ClientCredentialsOAuthProvider):
    """Jamf Pro OAuth provider using API Roles and Clients for API access."""

    id: ClassVar[str] = "jamf_pro"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="jamf_pro",
        name="Jamf Pro",
        description=(
            "Jamf Pro OAuth provider using API Roles and Clients "
            "(client credentials) for the Jamf Pro API."
        ),
        requires_config=True,
        enabled=True,
        api_docs_url=JAMF_PRO_API_DOCS_URL,
        setup_guide_url=JAMF_PRO_SETUP_GUIDE_URL,
        troubleshooting_url=JAMF_PRO_SETUP_GUIDE_URL,
    )
    default_authorization_endpoint: ClassVar[str | None] = (
        "https://{instance}.jamfcloud.com/api/oauth/token"
    )
    default_token_endpoint: ClassVar[str | None] = (
        "https://{instance}.jamfcloud.com/api/oauth/token"
    )
    token_endpoint_help: ClassVar[str | list[str] | None] = [
        "Replace {instance} with your Jamf Cloud subdomain, for example",
        "https://acme.jamfcloud.com/api/oauth/token",
        "\n",
        "Create the client under Settings > System > API roles and clients. "
        "Requires Jamf Pro 10.49 or later. Note that adding or removing an API "
        "role on an existing client requires rotating the client secret before "
        "the change takes effect.",
    ]

    def _get_token_endpoint_auth_method(self) -> str | None:
        return "client_secret_post"

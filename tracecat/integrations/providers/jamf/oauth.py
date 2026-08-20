"""Jamf Pro OAuth provider using client credentials flow."""

from typing import ClassVar

from tracecat.integrations.providers.base import ClientCredentialsOAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

JAMF_API_DOCS_URL = "https://developer.jamf.com/jamf-pro/reference/jamf-pro-api"
JAMF_SETUP_GUIDE_URL = "https://developer.jamf.com/jamf-pro/docs/client-credentials"


class JamfOAuthProvider(ClientCredentialsOAuthProvider):
    """Jamf Pro OAuth provider using API Roles and Clients for API access."""

    id: ClassVar[str] = "jamf"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="jamf",
        name="Jamf Pro",
        description=(
            "Jamf Pro OAuth provider using an API role and client (client credentials)."
        ),
        requires_config=True,
        enabled=True,
        api_docs_url=JAMF_API_DOCS_URL,
        setup_guide_url=JAMF_SETUP_GUIDE_URL,
        troubleshooting_url=JAMF_SETUP_GUIDE_URL,
    )
    # Jamf Pro only implements the client credentials grant, so there is no
    # consent URL. The base provider requires both endpoints to be set, so this
    # mirrors the token endpoint and is never used to start a consent flow.
    default_authorization_endpoint: ClassVar[str | None] = (
        "https://{instance}.jamfcloud.com/api/oauth/token"
    )
    default_token_endpoint: ClassVar[str | None] = (
        "https://{instance}.jamfcloud.com/api/oauth/token"
    )
    authorization_endpoint_help: ClassVar[str | list[str] | None] = [
        "Unused. Jamf Pro only supports the client credentials grant, so this "
        "endpoint is never called; leave it matching the token endpoint.",
    ]
    token_endpoint_help: ClassVar[str | list[str] | None] = [
        "Replace {instance} with your Jamf Cloud subdomain, for example:",
        "https://acme.jamfcloud.com/api/oauth/token",
        "\n",
        "Create the client under Settings > System > API roles and clients. "
        "Requires Jamf Pro 10.49 or later. Note that adding or removing an API "
        "role on an existing client requires rotating the client secret before "
        "the change takes effect.",
    ]

    def _get_token_endpoint_auth_method(self) -> str | None:
        # Jamf expects client_id and client_secret in a form-encoded body and
        # rejects HTTP Basic, so this overrides the base client_secret_basic.
        return "client_secret_post"

"""Zendesk OAuth provider using client credentials flow."""

from typing import ClassVar

from tracecat.integrations.providers.base import ClientCredentialsOAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

ZENDESK_API_DOCS_URL = (
    "https://developer.zendesk.com/api-reference/ticketing/introduction/"
)
ZENDESK_SETUP_GUIDE_URL = (
    "https://developer.zendesk.com/documentation/ticketing/authentication/"
    "creating-and-managing-oauth-clients/"
)


class ZendeskOAuthProvider(ClientCredentialsOAuthProvider):
    """Zendesk OAuth provider using a global OAuth client with client credentials."""

    id: ClassVar[str] = "zendesk"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="zendesk",
        name="Zendesk",
        description=(
            "Zendesk OAuth provider using a global OAuth client (client credentials)."
        ),
        requires_config=True,
        enabled=True,
        api_docs_url=ZENDESK_API_DOCS_URL,
        setup_guide_url=ZENDESK_SETUP_GUIDE_URL,
        troubleshooting_url=ZENDESK_SETUP_GUIDE_URL,
    )
    # Zendesk exposes both the authorization consent page and the token endpoint
    # on the same subdomain. Only the token endpoint is used for the client
    # credentials grant, but the base provider requires both fields to be set.
    default_authorization_endpoint: ClassVar[str | None] = (
        "https://{subdomain}.zendesk.com/oauth/authorizations/new"
    )
    default_token_endpoint: ClassVar[str | None] = (
        "https://{subdomain}.zendesk.com/oauth/tokens"
    )
    authorization_endpoint_help: ClassVar[str | list[str] | None] = [
        "Unused for the client credentials grant. Leave it pointing at the "
        "standard Zendesk consent URL for your subdomain, for example:",
        "https://acme.zendesk.com/oauth/authorizations/new",
    ]
    token_endpoint_help: ClassVar[str | list[str] | None] = [
        "Replace {subdomain} with your Zendesk subdomain, for example:",
        "https://acme.zendesk.com/oauth/tokens",
        "\n",
        "Create a global OAuth client at Admin Center > Apps and integrations "
        "> APIs > Zendesk API > OAuth Clients. The client credentials grant is "
        "only available for global OAuth clients; scopes such as 'read' and "
        "'write' are configured on the client itself.",
    ]

    def _get_token_endpoint_auth_method(self) -> str | None:
        # Zendesk expects client_id and client_secret in a form-encoded body.
        return "client_secret_post"

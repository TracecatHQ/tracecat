"""Cribl OAuth provider using the client credentials flow."""

from typing import Any, ClassVar
from urllib.parse import urlsplit

from tracecat.integrations.providers.base import ClientCredentialsOAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

CRIBL_API_DOCS_URL = "https://docs.cribl.io/cribl-as-code/api-reference/"
CRIBL_SETUP_GUIDE_URL = "https://docs.cribl.io/cribl-as-code/api-auth/"
CRIBL_TOKEN_ENDPOINT = "https://login.cribl.cloud/oauth/token"
CRIBL_AUDIENCE = "https://api.cribl.cloud"
CRIBL_GOV_TOKEN_HOST = "criblgov-prod.okta.com"
CRIBL_GOV_AUDIENCE = "https://api.cribl-gov.cloud"


class CriblOAuthProvider(ClientCredentialsOAuthProvider):
    """Cribl.Cloud API Credential using the client credentials grant."""

    id: ClassVar[str] = "cribl"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="cribl",
        name="Cribl",
        description=(
            "Cribl.Cloud API Credential (client credentials) for the Cribl "
            "control plane API in Cribl.Cloud, Cribl.Cloud Government, and "
            "hybrid deployments."
        ),
        requires_config=True,
        enabled=True,
        api_docs_url=CRIBL_API_DOCS_URL,
        setup_guide_url=CRIBL_SETUP_GUIDE_URL,
        troubleshooting_url=CRIBL_SETUP_GUIDE_URL,
    )
    # Cribl API Credentials only implement the client credentials grant, so
    # there is no consent URL. The base provider requires both endpoints, so
    # this mirrors the token endpoint and is never used to start a consent flow.
    default_authorization_endpoint: ClassVar[str | None] = CRIBL_TOKEN_ENDPOINT
    default_token_endpoint: ClassVar[str | None] = CRIBL_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | list[str] | None] = [
        "Unused. Cribl API Credentials only support the client credentials "
        "grant, so this endpoint is never called; leave it matching the token "
        "endpoint.",
    ]
    token_endpoint_help: ClassVar[str | list[str] | None] = [
        "Use https://login.cribl.cloud/oauth/token for Cribl.Cloud and hybrid "
        "deployments.",
        "For Cribl.Cloud Government, use "
        "https://criblgov-prod.okta.com/oauth2/ausfuanngyqh8CJ6c4h7/v1/token.",
        "\n",
        "Create the API Credential in Cribl.Cloud under Products > Cribl > "
        "Organization > API Credentials. Its Permissions determine which API "
        "operations the token can perform.",
    ]

    def _get_token_endpoint_auth_method(self) -> str | None:
        # Cribl documents client_id and client_secret in the token request body.
        return "client_secret_post"

    def _get_additional_token_params(self) -> dict[str, Any]:
        params = super()._get_additional_token_params()
        params["audience"] = self._audience()
        return params

    def _audience(self) -> str:
        if urlsplit(self.token_endpoint).hostname == CRIBL_GOV_TOKEN_HOST:
            return CRIBL_GOV_AUDIENCE
        return CRIBL_AUDIENCE

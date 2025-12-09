"""ServiceNow OAuth provider using client credentials flow."""

from typing import ClassVar

from tracecat.integrations.providers.base import ClientCredentialsOAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

SERVICENOW_TOKEN_DOCS_URL = "https://www.servicenow.com/docs/bundle/yokohama-api-reference/page/integrate/inbound-rest/reference/r_RESTOAuthExample.html"
SERVICENOW_API_DOCS_URL = "https://www.servicenow.com/docs/bundle/zurich-api-reference/page/build/applications/concept/api-rest.html"


class ServiceNowOAuthProvider(ClientCredentialsOAuthProvider):
    """ServiceNow OAuth provider using client credentials for API access."""

    id: ClassVar[str] = "servicenow"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="servicenow",
        name="ServiceNow",
        description="ServiceNow OAuth provider using client credentials for REST APIs.",
        setup_steps=[
            "Create an OAuth API endpoint (endpoint for external clients) in ServiceNow",
            "Note the generated client ID and client secret",
            "Configure the token endpoint URL for your instance",
            "Assign the appropriate OAuth scopes to the client",
        ],
        requires_config=True,
        enabled=True,
        api_docs_url=SERVICENOW_API_DOCS_URL,
        setup_guide_url=SERVICENOW_TOKEN_DOCS_URL,
        troubleshooting_url=SERVICENOW_TOKEN_DOCS_URL,
    )
    default_authorization_endpoint: ClassVar[str | None] = (
        "https://{instance}.service-now.com/oauth_auth.do"
    )
    default_token_endpoint: ClassVar[str | None] = (
        "https://{instance}.service-now.com/oauth_token.do"
    )

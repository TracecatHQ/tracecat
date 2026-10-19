"""Atlassian OAuth 2.0 (3LO) provider for Jira and Jira Service Management."""

from typing import Any, ClassVar

from tracecat.integrations.providers.base import AuthorizationCodeOAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

ATLASSIAN_AUTHORIZATION_URL = "https://auth.atlassian.com/authorize"
ATLASSIAN_TOKEN_URL = "https://auth.atlassian.com/oauth/token"
ATLASSIAN_API_AUDIENCE = "api.atlassian.com"
ATLASSIAN_SETUP_GUIDE_URL = (
    "https://developer.atlassian.com/cloud/jira/platform/oauth-2-3lo-apps/"
)
ATLASSIAN_API_DOCS_URL = "https://developer.atlassian.com/cloud/jira/platform/rest/v3/"
ATLASSIAN_SCOPES = [
    # Jira platform
    "manage:jira-configuration",
    "read:jira-user",
    "read:jira-work",
    "write:jira-work",
    # Jira Service Management requests
    "manage:servicedesk-customer",
    "read:servicedesk-request",
    "write:servicedesk-request",
    # Jira Service Management operations
    "read:ops-alert:jira-service-management",
    "write:ops-alert:jira-service-management",
    "read:ops-config:jira-service-management",
    "write:ops-config:jira-service-management",
    # Refresh tokens
    "offline_access",
]


class AtlassianOAuthProvider(AuthorizationCodeOAuthProvider):
    """Atlassian OAuth provider using the authorization code (3LO) flow."""

    id: ClassVar[str] = "atlassian"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=ATLASSIAN_SCOPES)
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="atlassian",
        name="Atlassian",
        description=(
            "Atlassian OAuth provider for Jira and Jira Service Management cloud APIs."
        ),
        setup_instructions=(
            "Create an OAuth 2.0 (3LO) integration in the Atlassian developer "
            "console, add the Jira and Jira Service Management API permissions, and "
            "register the Tracecat redirect URI as the callback URL. OAuth requests "
            "are routed through api.atlassian.com, so set the Jira base URL to "
            "https://api.atlassian.com/ex/jira/{cloudId} rather than your site URL."
        ),
        requires_config=True,
        enabled=True,
        api_docs_url=ATLASSIAN_API_DOCS_URL,
        setup_guide_url=ATLASSIAN_SETUP_GUIDE_URL,
        troubleshooting_url=ATLASSIAN_SETUP_GUIDE_URL,
    )
    default_authorization_endpoint: ClassVar[str | None] = ATLASSIAN_AUTHORIZATION_URL
    default_token_endpoint: ClassVar[str | None] = ATLASSIAN_TOKEN_URL

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        params = super()._get_additional_authorize_params()
        # Atlassian requires the API audience, and `prompt=consent` so the user
        # always sees the consent screen when scopes change.
        params["audience"] = ATLASSIAN_API_AUDIENCE
        params["prompt"] = "consent"
        return params

    def _get_token_endpoint_auth_method(self) -> str | None:
        # Atlassian expects client_id and client_secret in the token request
        # body rather than an HTTP Basic header.
        return "client_secret_post"

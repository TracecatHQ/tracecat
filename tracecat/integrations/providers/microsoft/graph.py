"""Microsoft Graph OAuth integration using standardized endpoint configuration."""

from typing import ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ClientCredentialsOAuthProvider,
)
from tracecat.integrations.providers.microsoft._common import (
    DEFAULT_COMMERCIAL_AUTHORIZATION_ENDPOINT as MS_DEFAULT_AUTH_ENDPOINT,
)
from tracecat.integrations.providers.microsoft._common import (
    DEFAULT_COMMERCIAL_TOKEN_ENDPOINT as MS_DEFAULT_TOKEN_ENDPOINT,
)
from tracecat.integrations.providers.microsoft._common import (
    MICROSOFT_AUTH_ENDPOINT_HELP,
    MICROSOFT_TOKEN_ENDPOINT_HELP,
)
from tracecat.integrations.providers.microsoft._common import (
    get_ac_setup_steps as _common_get_ac_setup_steps,
)
from tracecat.integrations.providers.microsoft._common import (
    get_cc_setup_steps as _common_get_cc_setup_steps,
)

DEFAULT_COMMERCIAL_AUTH_ENDPOINT = MS_DEFAULT_AUTH_ENDPOINT
DEFAULT_COMMERCIAL_TOKEN_ENDPOINT = MS_DEFAULT_TOKEN_ENDPOINT
GRAPH_AUTH_ENDPOINT_HELP = MICROSOFT_AUTH_ENDPOINT_HELP
GRAPH_TOKEN_ENDPOINT_HELP = MICROSOFT_TOKEN_ENDPOINT_HELP


def get_ac_setup_steps(service: str = "Microsoft Graph") -> list[str]:
    """Get setup steps for authorization code flow for a Microsoft service."""
    return _common_get_ac_setup_steps(service)


def get_cc_setup_steps(service: str = "Microsoft Graph") -> list[str]:
    """Get setup steps for client credentials flow for a Microsoft service."""
    return _common_get_cc_setup_steps(service)


AC_DESCRIPTION = "OAuth provider for delegated user permissions"
CC_DESCRIPTION = "OAuth provider for application permissions (service account)"


# Shared Microsoft Graph scopes for authorization code flow
AC_SCOPES = ProviderScopes(
    default=["offline_access", "https://graph.microsoft.com/User.Read"],
)


# Shared metadata for authorization code flow
AC_METADATA = ProviderMetadata(
    id="microsoft_graph",
    name="Microsoft Graph (Delegated)",
    description=f"Microsoft Graph {AC_DESCRIPTION}",
    setup_steps=get_ac_setup_steps(),
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/graph/auth-v2-user",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app",
    troubleshooting_url="https://learn.microsoft.com/en-us/graph/resolve-auth-errors",
)


class MicrosoftGraphACProvider(AuthorizationCodeOAuthProvider):
    """Microsoft Graph OAuth provider using authorization code flow for delegated user permissions."""

    id: ClassVar[str] = "microsoft_graph"
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    metadata: ClassVar[ProviderMetadata] = AC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_AUTH_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | None] = GRAPH_AUTH_ENDPOINT_HELP
    token_endpoint_help: ClassVar[str | None] = GRAPH_TOKEN_ENDPOINT_HELP


# Shared Microsoft Graph scopes for client credentials flow
CC_SCOPES = ProviderScopes(
    # Client credentials flow requires .default scope.
    # App permissions are configured in Azure Portal.
    default=["https://graph.microsoft.com/.default"],
)

# Shared metadata for client credentials flow
CC_METADATA = ProviderMetadata(
    id="microsoft_graph",
    name="Microsoft Graph (Service Principal)",
    description=f"Microsoft Graph {CC_DESCRIPTION}",
    setup_steps=get_cc_setup_steps(),
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/graph/auth-v2-service",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-client-creds-grant-flow",
    troubleshooting_url="https://learn.microsoft.com/en-us/graph/resolve-auth-errors",
)


class MicrosoftGraphCCProvider(ClientCredentialsOAuthProvider):
    """Microsoft Graph OAuth provider using client credentials flow for application permissions (service account)."""

    id: ClassVar[str] = "microsoft_graph"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    metadata: ClassVar[ProviderMetadata] = CC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_AUTH_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | None] = GRAPH_AUTH_ENDPOINT_HELP
    token_endpoint_help: ClassVar[str | None] = GRAPH_TOKEN_ENDPOINT_HELP

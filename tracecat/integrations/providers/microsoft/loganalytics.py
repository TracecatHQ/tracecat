"""Azure Log Analytics OAuth integration for KQL query execution."""

from typing import Any, ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ClientCredentialsOAuthProvider,
)

DEFAULT_COMMERCIAL_AUTH_ENDPOINT = (
    "https://login.microsoftonline.com/common/oauth2/v2.0/authorize"
)
DEFAULT_COMMERCIAL_TOKEN_ENDPOINT = (
    "https://login.microsoftonline.com/common/oauth2/v2.0/token"
)
LOG_ANALYTICS_AUTH_HELP = (
    "Most tenants use the commercial cloud. Sovereign options:"
    "\n- Commercial: https://login.microsoftonline.com/{tenant}/oauth2/v2.0/authorize"
    "\n- US Gov: https://login.microsoftonline.us/{tenant}/oauth2/v2.0/authorize"
)
LOG_ANALYTICS_TOKEN_HELP = (
    "Most tenants use the commercial cloud. Sovereign options:"
    "\n- Commercial: https://login.microsoftonline.com/{tenant}/oauth2/v2.0/token"
    "\n- US Gov: https://login.microsoftonline.us/{tenant}/oauth2/v2.0/token"
)

SETUP_STEPS = [
    "Register your application in Azure Portal",
    "Add the redirect URI shown above to 'Redirect URIs'",
    "Configure Azure Log Analytics API permissions for both delegated (user) and application (service principal) access",
    "Add Data.Read permission and grant consent for the scopes you plan to use",
    "Copy Client ID and Client Secret",
    "Configure the authorization and token endpoints for your tenant (defaults use the commercial cloud with 'common')",
]


AC_DESCRIPTION = "OAuth provider for Azure Log Analytics KQL query execution"
AC_SCOPES = ProviderScopes(
    default=[
        "offline_access",
        "https://api.loganalytics.io/user_impersonation",
    ],
)
AC_METADATA = ProviderMetadata(
    id="azure_log_analytics",
    name="Azure Log Analytics (Delegated)",
    description=f"Azure Log Analytics {AC_DESCRIPTION}",
    setup_steps=SETUP_STEPS,
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/rest/api/loganalytics/",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/overview",
    troubleshooting_url="https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/errors",
)


class AzureLogAnalyticsACProvider(AuthorizationCodeOAuthProvider):
    """Azure Log Analytics OAuth provider for KQL query execution with delegated permissions."""

    id: ClassVar[str] = "azure_log_analytics"
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    metadata: ClassVar[ProviderMetadata] = AC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_AUTH_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | None] = LOG_ANALYTICS_AUTH_HELP
    token_endpoint_help: ClassVar[str | None] = LOG_ANALYTICS_TOKEN_HELP

    def _get_additional_authorize_params(self) -> dict[str, Any]:
        """Add Azure-specific authorization parameters."""
        return {
            "response_mode": "query",
            "prompt": "select_account",
        }


CC_SCOPES = ProviderScopes(
    default=["https://api.loganalytics.io/.default"],
)

CC_METADATA = ProviderMetadata(
    id="azure_log_analytics",
    name="Azure Log Analytics (Service Principal)",
    description="Azure Log Analytics service principal authentication for automated KQL execution",
    setup_steps=SETUP_STEPS,
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/rest/api/loganalytics/",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/overview",
    troubleshooting_url="https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/errors",
)


class AzureLogAnalyticsCCProvider(ClientCredentialsOAuthProvider):
    """Azure Log Analytics OAuth provider using service principal authentication."""

    id: ClassVar[str] = "azure_log_analytics"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    metadata: ClassVar[ProviderMetadata] = CC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_AUTH_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_COMMERCIAL_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | None] = LOG_ANALYTICS_AUTH_HELP
    token_endpoint_help: ClassVar[str | None] = LOG_ANALYTICS_TOKEN_HELP

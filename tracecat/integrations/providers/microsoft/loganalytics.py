"""Azure Log Analytics OAuth integration for KQL query execution."""

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

DEFAULT_COMMERCIAL_AUTH_ENDPOINT = MS_DEFAULT_AUTH_ENDPOINT
DEFAULT_COMMERCIAL_TOKEN_ENDPOINT = MS_DEFAULT_TOKEN_ENDPOINT
LOG_ANALYTICS_AUTH_HELP = MICROSOFT_AUTH_ENDPOINT_HELP
LOG_ANALYTICS_TOKEN_HELP = MICROSOFT_TOKEN_ENDPOINT_HELP

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

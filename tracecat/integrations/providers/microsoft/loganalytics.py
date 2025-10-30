"""Azure Log Analytics OAuth integration for KQL query execution."""

from typing import ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ClientCredentialsOAuthProvider,
)
from tracecat.integrations.providers.microsoft._common import (
    DEFAULT_AUTHORIZATION_ENDPOINT,
    DEFAULT_TOKEN_ENDPOINT,
    get_ac_setup_steps,
    get_cc_setup_steps,
)

AC_SCOPES = ProviderScopes(
    default=[
        "offline_access",
        "https://api.loganalytics.io/user_impersonation",
    ],
)
CC_SCOPES = ProviderScopes(
    default=["https://api.loganalytics.io/.default"],
)

AC_METADATA = ProviderMetadata(
    id="azure_log_analytics",
    name="Azure Log Analytics (Delegated)",
    description="Azure Log Analytics delegated authentication for KQL query execution",
    setup_steps=get_ac_setup_steps(service="Azure Log Analytics"),
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/rest/api/loganalytics/",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/overview",
    troubleshooting_url="https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/errors",
)

CC_METADATA = ProviderMetadata(
    id="azure_log_analytics",
    name="Azure Log Analytics (Service Principal)",
    description="Azure Log Analytics service principal authentication for KQL query execution",
    setup_steps=get_cc_setup_steps(service="Azure Log Analytics"),
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/rest/api/loganalytics/",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/overview",
    troubleshooting_url="https://learn.microsoft.com/en-us/azure/azure-monitor/logs/api/errors",
)


class AzureLogAnalyticsACProvider(AuthorizationCodeOAuthProvider):
    """Azure Log Analytics OAuth provider for KQL query execution with delegated permissions."""

    default_tenant: ClassVar[str] = "organizations"
    id: ClassVar[str] = "azure_log_analytics"
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    metadata: ClassVar[ProviderMetadata] = AC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_AUTHORIZATION_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_TOKEN_ENDPOINT


class AzureLogAnalyticsCCProvider(ClientCredentialsOAuthProvider):
    """Azure Log Analytics OAuth provider using service principal authentication."""

    default_tenant: ClassVar[str] = "organizations"
    id: ClassVar[str] = "azure_log_analytics"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    metadata: ClassVar[ProviderMetadata] = CC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_AUTHORIZATION_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_TOKEN_ENDPOINT

"""Azure Log Analytics OAuth integration for KQL query execution."""

from typing import ClassVar

from tracecat.integrations.providers.microsoft.azure.provider import (
    AzureManagementACProvider,
    AzureManagementCCProvider,
    get_azure_ac_metadata,
    get_azure_cc_metadata,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class AzureLogAnalyticsACProvider(AzureManagementACProvider):
    """Azure Log Analytics OAuth provider for KQL query execution with delegated permissions."""

    id: ClassVar[str] = "azure_log_analytics"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "offline_access",
            "https://api.loganalytics.io/user_impersonation",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_azure_ac_metadata(
        id="azure_log_analytics",
        name="Azure Log Analytics",
    )


class AzureLogAnalyticsCCProvider(AzureManagementCCProvider):
    """Azure Log Analytics OAuth provider using service principal authentication."""

    id: ClassVar[str] = "azure_log_analytics"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["https://api.loganalytics.io/.default"],
    )
    metadata: ClassVar[ProviderMetadata] = get_azure_cc_metadata(
        id="azure_log_analytics",
        name="Azure Log Analytics",
    )

"""Microsoft Sentinel OAuth integration using Azure Management provider."""

from typing import ClassVar

from tracecat.integrations.providers.microsoft.azure.provider import (
    AzureManagementACProvider,
    AzureManagementCCProvider,
    get_azure_ac_metadata,
    get_azure_cc_metadata,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class MicrosoftSentinelACProvider(AzureManagementACProvider):
    """Microsoft Sentinel OAuth provider using authorization code flow for delegated user permissions."""

    id: ClassVar[str] = "microsoft_sentinel"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "offline_access",
            "https://management.azure.com/user_impersonation",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_azure_ac_metadata(
        id="microsoft_sentinel",
        name="Microsoft Sentinel",
    )


class MicrosoftSentinelCCProvider(AzureManagementCCProvider):
    """Microsoft Sentinel OAuth provider using client credentials flow for application permissions."""

    id: ClassVar[str] = "microsoft_sentinel"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["https://management.azure.com/.default"],
    )
    metadata: ClassVar[ProviderMetadata] = get_azure_cc_metadata(
        id="microsoft_sentinel",
        name="Microsoft Sentinel",
    )

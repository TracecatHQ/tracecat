"""Microsoft Sentinel OAuth integration using Azure Management provider."""

from typing import ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.microsoft.azure import (
    AC_DEFAULT_SCOPES,
    AC_DESCRIPTION,
    AzureManagementACProvider,
    get_azure_setup_steps,
)


def get_sentinel_setup_steps() -> list[str]:
    """Get Sentinel-specific setup steps for authorization code flow."""
    return get_azure_setup_steps("Microsoft Sentinel")


AC_SCOPES = ProviderScopes(
    default=AC_DEFAULT_SCOPES,
)

AC_METADATA = ProviderMetadata(
    id="microsoft_sentinel",
    name="Microsoft Sentinel (Delegated)",
    description=f"Microsoft Sentinel {AC_DESCRIPTION}",
    setup_steps=get_sentinel_setup_steps(),
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/rest/api/securityinsights/",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/sentinel/",
    troubleshooting_url="https://learn.microsoft.com/en-us/azure/sentinel/troubleshooting",
)


class MicrosoftSentinelACProvider(AzureManagementACProvider):
    """Microsoft Sentinel OAuth provider using authorization code flow for delegated user permissions."""

    id: ClassVar[str] = "microsoft_sentinel"
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    metadata: ClassVar[ProviderMetadata] = AC_METADATA

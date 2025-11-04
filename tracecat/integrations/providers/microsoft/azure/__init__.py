"""Azure OAuth providers."""

from tracecat.integrations.providers.microsoft.azure.provider import (
    AzureManagementACProvider,
    AzureManagementCCProvider,
)

__all__ = ["AzureManagementACProvider", "AzureManagementCCProvider"]

"""Azure OAuth providers."""

from tracecat.integrations.providers.microsoft.azure.loganalytics import (
    AzureLogAnalyticsACProvider,
    AzureLogAnalyticsCCProvider,
)
from tracecat.integrations.providers.microsoft.azure.provider import (
    AzureManagementACProvider,
    AzureManagementCCProvider,
)
from tracecat.integrations.providers.microsoft.azure.sentinel import (
    MicrosoftSentinelACProvider,
    MicrosoftSentinelCCProvider,
)

__all__ = [
    "AzureManagementACProvider",
    "AzureManagementCCProvider",
    "MicrosoftSentinelACProvider",
    "MicrosoftSentinelCCProvider",
    "AzureLogAnalyticsACProvider",
    "AzureLogAnalyticsCCProvider",
]

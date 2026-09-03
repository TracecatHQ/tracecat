"""Microsoft Graph Security OAuth integration built on Microsoft Graph providers."""

from typing import ClassVar

from tracecat.integrations.providers.microsoft.graph.provider import (
    GRAPH_NATIONAL_CLOUD_SETUP_INSTRUCTIONS,
    MicrosoftGraphACProvider,
    MicrosoftGraphCCProvider,
    get_graph_ac_metadata,
    get_graph_cc_metadata,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

GRAPH_SECURITY_API_DOCS_URL = (
    "https://learn.microsoft.com/en-us/graph/api/resources/security-api-overview"
)


class MicrosoftGraphSecurityACProvider(MicrosoftGraphACProvider):
    """Microsoft Graph Security OAuth provider for delegated permissions."""

    id: ClassVar[str] = "microsoft_graph_security"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "offline_access",
            "https://graph.microsoft.com/SecurityAlert.ReadWrite.All",
            "https://graph.microsoft.com/SecurityIncident.ReadWrite.All",
            "https://graph.microsoft.com/SecurityEvents.Read.All",
            "https://graph.microsoft.com/ThreatHunting.Read.All",
            "https://graph.microsoft.com/ThreatIntelligence.Read.All",
            "https://graph.microsoft.com/AuditLogsQuery-Entra.Read.All",
            "https://graph.microsoft.com/SecurityData.Manage.All",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_graph_ac_metadata(
        id="microsoft_graph_security",
        name="Microsoft Graph Security",
        api_docs_url=GRAPH_SECURITY_API_DOCS_URL,
        setup_instructions=GRAPH_NATIONAL_CLOUD_SETUP_INSTRUCTIONS,
    )


class MicrosoftGraphSecurityCCProvider(MicrosoftGraphCCProvider):
    """Microsoft Graph Security OAuth provider for application permissions."""

    id: ClassVar[str] = "microsoft_graph_security"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["https://graph.microsoft.com/.default"],
    )
    metadata: ClassVar[ProviderMetadata] = get_graph_cc_metadata(
        id="microsoft_graph_security",
        name="Microsoft Graph Security",
        api_docs_url=GRAPH_SECURITY_API_DOCS_URL,
        setup_instructions=GRAPH_NATIONAL_CLOUD_SETUP_INSTRUCTIONS,
    )

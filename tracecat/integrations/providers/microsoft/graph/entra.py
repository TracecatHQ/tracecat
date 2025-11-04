"""Microsoft Entra ID OAuth integration built on Microsoft Graph providers."""

from typing import ClassVar

from tracecat.integrations.providers.microsoft.graph.provider import (
    MicrosoftGraphACProvider,
    MicrosoftGraphCCProvider,
    get_graph_ac_metadata,
    get_graph_cc_metadata,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class MicrosoftEntraACProvider(MicrosoftGraphACProvider):
    """Microsoft Entra ID OAuth provider for delegated permissions."""

    id: ClassVar[str] = "microsoft_entra"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=[
            "offline_access",
            "https://graph.microsoft.com/User.ReadWrite.All",
            "https://graph.microsoft.com/Group.ReadWrite.All",
            "https://graph.microsoft.com/Directory.ReadWrite.All",
        ],
    )
    metadata: ClassVar[ProviderMetadata] = get_graph_ac_metadata(
        id="microsoft_entra",
        name="Microsoft Entra ID",
    )


class MicrosoftEntraCCProvider(MicrosoftGraphCCProvider):
    """Microsoft Entra ID OAuth provider for application permissions."""

    id: ClassVar[str] = "microsoft_entra"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["https://graph.microsoft.com/.default"],
    )
    metadata: ClassVar[ProviderMetadata] = get_graph_cc_metadata(
        id="microsoft_entra",
        name="Microsoft Entra ID",
    )

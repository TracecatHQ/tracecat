"""Microsoft Entra ID OAuth integration built on Microsoft Graph providers."""

from typing import ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.microsoft.graph import (
    MicrosoftGraphACProvider,
    MicrosoftGraphCCProvider,
    get_ac_setup_steps,
    get_cc_setup_steps,
)

# Microsoft Entra delegated operations require directory and group write scopes.
ENTRA_AC_SCOPES = ProviderScopes(
    default=[
        "offline_access",
        "https://graph.microsoft.com/User.ReadWrite.All",
        "https://graph.microsoft.com/Group.ReadWrite.All",
        "https://graph.microsoft.com/Directory.ReadWrite.All",
    ],
)

ENTRA_CC_SCOPES = ProviderScopes(
    default=[
        "https://graph.microsoft.com/.default",
    ]
)

ENTRA_API_DOC_URL = "https://learn.microsoft.com/en-us/graph/api/resources/identity-network-access-overview?view=graph-rest-1.0"


AC_METADATA = ProviderMetadata(
    id="microsoft_entra",
    name="Microsoft Entra ID (Delegated)",
    description="Microsoft Entra ID delegated access using Microsoft Graph scopes.",
    setup_steps=get_ac_setup_steps("Microsoft Entra ID"),
    enabled=True,
    api_docs_url=ENTRA_API_DOC_URL,
    setup_guide_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app",
    troubleshooting_url="https://learn.microsoft.com/en-us/graph/resolve-auth-errors",
)


class MicrosoftEntraACProvider(MicrosoftGraphACProvider):
    """Microsoft Entra ID OAuth provider for delegated permissions."""

    id: ClassVar[str] = "microsoft_entra"
    scopes: ClassVar[ProviderScopes] = ENTRA_AC_SCOPES
    metadata: ClassVar[ProviderMetadata] = AC_METADATA


CC_METADATA = ProviderMetadata(
    id="microsoft_entra",
    name="Microsoft Entra ID (Service account)",
    description="Microsoft Entra ID service principal access using Microsoft Graph.",
    setup_steps=get_cc_setup_steps("Microsoft Entra ID"),
    enabled=True,
    api_docs_url=ENTRA_API_DOC_URL,
    setup_guide_url="https://learn.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-client-creds-grant-flow",
    troubleshooting_url="https://learn.microsoft.com/en-us/graph/resolve-auth-errors",
)


class MicrosoftEntraCCProvider(MicrosoftGraphCCProvider):
    """Microsoft Entra ID OAuth provider for application permissions."""

    id: ClassVar[str] = "microsoft_entra"
    scopes: ClassVar[ProviderScopes] = ENTRA_CC_SCOPES
    metadata: ClassVar[ProviderMetadata] = CC_METADATA

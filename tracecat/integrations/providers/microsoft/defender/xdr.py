"""Microsoft Defender for XDR OAuth integrations."""

from typing import ClassVar

from tracecat.integrations.providers.microsoft.common import (
    MicrosoftAuthorizationCodeOAuthProvider,
    MicrosoftClientCredentialsOAuthProvider,
    get_ac_description,
    get_cc_description,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class MicrosoftDefenderXDRACProvider(MicrosoftAuthorizationCodeOAuthProvider):
    """Microsoft Defender for XDR OAuth provider using authorization code flow."""

    id: ClassVar[str] = "microsoft_defender_xdr"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["offline_access", "https://api.security.microsoft.com/.default"],
    )
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="microsoft_defender_xdr",
        name="Microsoft Defender for XDR (Delegated)",
        description=get_ac_description("Microsoft Defender for XDR"),
        requires_config=True,
        enabled=True,
        api_docs_url="https://learn.microsoft.com/en-us/defender-xdr/api-access",
        setup_guide_url="https://learn.microsoft.com/en-us/defender-xdr/api-create-app-user-context",
        troubleshooting_url="https://learn.microsoft.com/en-us/defender-xdr/api-error-codes",
    )


class MicrosoftDefenderXDRCCProvider(MicrosoftClientCredentialsOAuthProvider):
    """Microsoft Defender for XDR OAuth provider using client credentials flow."""

    id: ClassVar[str] = "microsoft_defender_xdr"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["https://api.security.microsoft.com/.default"],
    )
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="microsoft_defender_xdr",
        name="Microsoft Defender for XDR (Service principal)",
        description=get_cc_description("Microsoft Defender for XDR"),
        requires_config=True,
        enabled=True,
        api_docs_url="https://learn.microsoft.com/en-us/defender-xdr/api-access",
        setup_guide_url="https://learn.microsoft.com/en-us/defender-xdr/api-create-app-web",
        troubleshooting_url="https://learn.microsoft.com/en-us/defender-xdr/api-error-codes",
    )

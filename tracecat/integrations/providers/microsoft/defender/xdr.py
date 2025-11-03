"""Microsoft Defender for XDR OAuth integrations."""

from typing import ClassVar

from tracecat.integrations.providers.microsoft.common import (
    ENTRA_ID_SETUP_STEPS,
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
        default=["offline_access", "https://api.securitycenter.microsoft.com/.default"],
    )
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="microsoft_defender_xdr",
        name="Microsoft Defender for XDR (Delegated)",
        description=get_ac_description("Microsoft Defender for XDR"),
        setup_steps=ENTRA_ID_SETUP_STEPS,
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
        default=["https://api.securitycenter.microsoft.com/.default"],
    )
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="microsoft_defender_xdr",
        name="Microsoft Defender for XDR (Service Principal)",
        description=get_cc_description("Microsoft Defender for XDR"),
        setup_steps=ENTRA_ID_SETUP_STEPS,
        requires_config=True,
        enabled=True,
        api_docs_url="https://learn.microsoft.com/en-us/defender-xdr/api-access",
        setup_guide_url="https://learn.microsoft.com/en-us/defender-xdr/api-create-app-web",
        troubleshooting_url="https://learn.microsoft.com/en-us/defender-xdr/api-error-codes",
    )

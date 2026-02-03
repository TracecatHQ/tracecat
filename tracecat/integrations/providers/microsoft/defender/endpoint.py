"""Microsoft Defender for Endpoint OAuth integrations."""

from typing import ClassVar

from tracecat.integrations.providers.microsoft.common import (
    MicrosoftAuthorizationCodeOAuthProvider,
    MicrosoftClientCredentialsOAuthProvider,
    get_ac_description,
    get_cc_description,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes


class MicrosoftDefenderEndpointACProvider(MicrosoftAuthorizationCodeOAuthProvider):
    """Microsoft Defender for Endpoint OAuth provider using authorization code flow."""

    id: ClassVar[str] = "microsoft_defender_endpoint"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["offline_access", "https://api.securitycenter.microsoft.com/.default"],
    )
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="microsoft_defender_endpoint",
        name="Microsoft Defender for Endpoint (Delegated)",
        description=get_ac_description("Microsoft Defender for Endpoint"),
        requires_config=True,
        enabled=True,
        api_docs_url="https://learn.microsoft.com/en-us/defender-endpoint/api/",
        setup_guide_url="https://learn.microsoft.com/en-us/defender-endpoint/api/exposed-apis-create-app-nativeapp",
        troubleshooting_url="https://learn.microsoft.com/en-us/defender-endpoint/api/common-errors",
    )


class MicrosoftDefenderEndpointCCProvider(MicrosoftClientCredentialsOAuthProvider):
    """Microsoft Defender for Endpoint OAuth provider using client credentials flow."""

    id: ClassVar[str] = "microsoft_defender_endpoint"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["https://api.securitycenter.microsoft.com/.default"],
    )
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="microsoft_defender_endpoint",
        name="Microsoft Defender for Endpoint (Service principal)",
        description=get_cc_description("Microsoft Defender for Endpoint"),
        requires_config=True,
        enabled=True,
        api_docs_url="https://learn.microsoft.com/en-us/defender-endpoint/api/",
        setup_guide_url="https://learn.microsoft.com/en-us/defender-endpoint/api/exposed-apis-create-app-webapp?tabs=PowerShell",
        troubleshooting_url="https://learn.microsoft.com/en-us/defender-endpoint/api/common-errors",
    )

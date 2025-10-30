"""Microsoft Defender for Endpoint OAuth integration."""

from __future__ import annotations

from typing import ClassVar

from tracecat.integrations.models import ProviderMetadata, ProviderScopes
from tracecat.integrations.providers.base import (
    AuthorizationCodeOAuthProvider,
    ClientCredentialsOAuthProvider,
)
from tracecat.integrations.providers.microsoft._common import (
    DEFAULT_AUTHORIZATION_ENDPOINT,
    DEFAULT_TOKEN_ENDPOINT,
    get_ac_setup_steps,
    get_cc_setup_steps,
)

AC_SCOPES = ProviderScopes(
    default=[
        "offline_access",
        "https://api.securitycenter.microsoft.com/.default",
    ],
)

CC_SCOPES = ProviderScopes(
    default=["https://api.securitycenter.microsoft.com/.default"],
)


AC_METADATA = ProviderMetadata(
    id="microsoft_defender_endpoint",
    name="Microsoft Defender for Endpoint (Delegated)",
    description="Microsoft Defender for Endpoint delegated authentication for investigation and response APIs.",
    setup_steps=get_ac_setup_steps(service="Microsoft Defender for Endpoint"),
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/defender-endpoint/api/",
    setup_guide_url="https://learn.microsoft.com/en-us/defender-endpoint/api/get-started",
    troubleshooting_url="https://learn.microsoft.com/en-us/defender-endpoint/api/common-errors",
)

CC_METADATA = ProviderMetadata(
    id="microsoft_defender_endpoint",
    name="Microsoft Defender for Endpoint (Service Principal)",
    description="Microsoft Defender for Endpoint service principal authentication for automated investigation and response.",
    setup_steps=get_cc_setup_steps(service="Microsoft Defender for Endpoint"),
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/defender-endpoint/api/",
    setup_guide_url="https://learn.microsoft.com/en-us/defender-endpoint/api/get-started",
    troubleshooting_url="https://learn.microsoft.com/en-us/defender-endpoint/api/common-errors",
)


class MicrosoftDefenderEndpointACProvider(AuthorizationCodeOAuthProvider):
    """Microsoft Defender for Endpoint OAuth provider for delegated user permissions."""

    default_tenant: ClassVar[str] = "organizations"
    id: ClassVar[str] = "microsoft_defender_endpoint"
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    metadata: ClassVar[ProviderMetadata] = AC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_AUTHORIZATION_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_TOKEN_ENDPOINT


class MicrosoftDefenderEndpointCCProvider(ClientCredentialsOAuthProvider):
    """Microsoft Defender for Endpoint OAuth provider using client credentials flow."""

    default_tenant: ClassVar[str] = "organizations"
    id: ClassVar[str] = "microsoft_defender_endpoint"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    metadata: ClassVar[ProviderMetadata] = CC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_AUTHORIZATION_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_TOKEN_ENDPOINT

"""Microsoft Sentinel OAuth integration using Azure Management provider."""

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
        "https://api.securityinsights.microsoft.com/.default",
    ],
)

CC_SCOPES = ProviderScopes(
    default=["https://api.securityinsights.microsoft.com/.default"],
)

AC_METADATA = ProviderMetadata(
    id="microsoft_sentinel",
    name="Microsoft Sentinel (Delegated)",
    description="Microsoft Sentinel delegated authentication for security insights and response",
    setup_steps=get_ac_setup_steps(service="Microsoft Sentinel"),
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/rest/api/securityinsights/",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/sentinel/",
    troubleshooting_url="https://learn.microsoft.com/en-us/azure/sentinel/troubleshooting",
)

CC_METADATA = ProviderMetadata(
    id="microsoft_sentinel",
    name="Microsoft Sentinel (Service Principal)",
    description="Microsoft Sentinel service principal authentication for security insights and response",
    setup_steps=get_cc_setup_steps(service="Microsoft Sentinel"),
    requires_config=True,
    enabled=True,
    api_docs_url="https://learn.microsoft.com/en-us/rest/api/securityinsights/",
    setup_guide_url="https://learn.microsoft.com/en-us/azure/sentinel/",
    troubleshooting_url="https://learn.microsoft.com/en-us/azure/sentinel/troubleshooting",
)


class MicrosoftSentinelACProvider(AuthorizationCodeOAuthProvider):
    """Microsoft Sentinel OAuth provider using authorization code flow for delegated user permissions."""

    id: ClassVar[str] = "microsoft_sentinel"
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    metadata: ClassVar[ProviderMetadata] = AC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_AUTHORIZATION_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_TOKEN_ENDPOINT


class MicrosoftSentinelCCProvider(ClientCredentialsOAuthProvider):
    """Microsoft Sentinel OAuth provider using client credentials flow for application permissions (service account)."""

    id: ClassVar[str] = "microsoft_sentinel"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    metadata: ClassVar[ProviderMetadata] = CC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_AUTHORIZATION_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_TOKEN_ENDPOINT

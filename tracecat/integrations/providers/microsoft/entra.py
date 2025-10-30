"""Microsoft Entra ID OAuth integration built on Microsoft Graph providers."""

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

API_DOCS_URL = "https://learn.microsoft.com/en-us/graph/overview"
AC_SETUP_GUIDE_URL = "https://learn.microsoft.com/en-us/azure/active-directory/develop/quickstart-register-app"
CC_SETUP_GUIDE_URL = "https://learn.microsoft.com/en-us/azure/active-directory/develop/v2-oauth2-client-creds-grant-flow"
TROUBLESHOOTING_URL = "https://learn.microsoft.com/en-us/graph/resolve-auth-errors"


AC_SCOPES = ProviderScopes(
    default=[
        "offline_access",
        "https://graph.microsoft.com/User.ReadWrite.All",
        "https://graph.microsoft.com/Group.ReadWrite.All",
        "https://graph.microsoft.com/Directory.ReadWrite.All",
    ],
)

CC_SCOPES = ProviderScopes(
    default=[
        "https://graph.microsoft.com/.default",
    ]
)


AC_METADATA = ProviderMetadata(
    id="microsoft_entra",
    name="Microsoft Entra ID (Delegated)",
    description="Microsoft Entra ID delegated access using Microsoft Graph scopes.",
    setup_steps=get_ac_setup_steps("Microsoft Entra ID"),
    requires_config=True,
    enabled=True,
    api_docs_url=API_DOCS_URL,
    setup_guide_url=AC_SETUP_GUIDE_URL,
    troubleshooting_url=TROUBLESHOOTING_URL,
)


CC_METADATA = ProviderMetadata(
    id="microsoft_entra",
    name="Microsoft Entra ID (Service Principal)",
    description="Microsoft Entra ID service principal authentication for Microsoft Graph APIs.",
    setup_steps=get_cc_setup_steps("Microsoft Entra ID"),
    requires_config=True,
    enabled=True,
    api_docs_url=API_DOCS_URL,
    setup_guide_url=CC_SETUP_GUIDE_URL,
    troubleshooting_url=TROUBLESHOOTING_URL,
)


class MicrosoftEntraACProvider(AuthorizationCodeOAuthProvider):
    """Microsoft Entra ID OAuth provider for delegated permissions."""

    id: ClassVar[str] = "microsoft_entra"
    scopes: ClassVar[ProviderScopes] = AC_SCOPES
    metadata: ClassVar[ProviderMetadata] = AC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_AUTHORIZATION_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_TOKEN_ENDPOINT


class MicrosoftEntraCCProvider(ClientCredentialsOAuthProvider):
    """Microsoft Entra ID OAuth provider for application permissions (service principal)."""

    id: ClassVar[str] = "microsoft_entra"
    scopes: ClassVar[ProviderScopes] = CC_SCOPES
    metadata: ClassVar[ProviderMetadata] = CC_METADATA
    default_authorization_endpoint: ClassVar[str] = DEFAULT_AUTHORIZATION_ENDPOINT
    default_token_endpoint: ClassVar[str] = DEFAULT_TOKEN_ENDPOINT

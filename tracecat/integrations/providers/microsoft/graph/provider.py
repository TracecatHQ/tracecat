"""Microsoft Graph OAuth integration using standardized OAuth providers."""

from typing import ClassVar

from tracecat.integrations.providers.microsoft.common import (
    MICROSOFT_SETUP_STEPS,
    MicrosoftAuthorizationCodeOAuthProvider,
    MicrosoftClientCredentialsOAuthProvider,
    get_ac_description,
    get_cc_description,
)
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

GRAPH_API_DOCS_URL = "https://learn.microsoft.com/en-us/graph/api/overview"
GRAPH_SETUP_GUIDE_URL = "https://learn.microsoft.com/en-us/graph/auth-register-app-v2"
GRAPH_TROUBLESHOOT_URL = "https://learn.microsoft.com/en-us/graph/resolve-auth-errors"


def get_graph_ac_metadata(
    id: str,
    name: str,
    api_docs_url: str = GRAPH_API_DOCS_URL,
    setup_guide_url: str = GRAPH_SETUP_GUIDE_URL,
    troubleshooting_url: str = GRAPH_TROUBLESHOOT_URL,
) -> ProviderMetadata:
    return ProviderMetadata(
        id=id,
        name=f"{name} (Delegated)",
        description=get_ac_description(name),
        setup_steps=MICROSOFT_SETUP_STEPS,
        requires_config=True,
        enabled=True,
        api_docs_url=api_docs_url,
        setup_guide_url=setup_guide_url,
        troubleshooting_url=troubleshooting_url,
    )


def get_graph_cc_metadata(
    id: str,
    name: str,
    api_docs_url: str = GRAPH_API_DOCS_URL,
    setup_guide_url: str = GRAPH_SETUP_GUIDE_URL,
    troubleshooting_url: str = GRAPH_TROUBLESHOOT_URL,
) -> ProviderMetadata:
    return ProviderMetadata(
        id=id,
        name=f"{name} (Service account)",
        description=get_cc_description(name),
        setup_steps=MICROSOFT_SETUP_STEPS,
        requires_config=True,
        enabled=True,
        api_docs_url=api_docs_url,
        setup_guide_url=setup_guide_url,
        troubleshooting_url=troubleshooting_url,
    )


class MicrosoftGraphACProvider(MicrosoftAuthorizationCodeOAuthProvider):
    """Microsoft Graph OAuth provider using authorization code flow for delegated user permissions."""

    id: ClassVar[str] = "microsoft_graph"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["offline_access", "https://graph.microsoft.com/User.Read"],
    )
    metadata: ClassVar[ProviderMetadata] = get_graph_ac_metadata(
        id="microsoft_graph",
        name="Microsoft Graph",
    )


class MicrosoftGraphCCProvider(MicrosoftClientCredentialsOAuthProvider):
    """Microsoft Graph OAuth provider using client credentials flow for application permissions (service account)."""

    id: ClassVar[str] = "microsoft_graph"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(
        default=["https://graph.microsoft.com/.default"],
    )
    metadata: ClassVar[ProviderMetadata] = get_graph_cc_metadata(
        id="microsoft_graph",
        name="Microsoft Graph",
    )

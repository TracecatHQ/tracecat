"""Credential declarations for the unified Microsoft Graph SDK namespace.

This module registers no actions. The generic SDK selects one product-scoped
credential chain while every request continues to use the shared Graph transport.
"""

from typing import Annotated, Literal

from pydantic import Field

from tracecat_registry import RegistryOAuthSecret, RegistrySecretType
from tracecat_registry.integrations._microsoft_graph_transport import GraphProduct

microsoft_graph_service_oauth_secret = RegistryOAuthSecret(
    provider_id="microsoft_graph",
    grant_type="client_credentials",
    optional=True,
)
"""Microsoft Graph application (service account) OAuth credentials."""

microsoft_graph_user_oauth_secret = RegistryOAuthSecret(
    provider_id="microsoft_graph",
    grant_type="authorization_code",
    optional=True,
)
"""Microsoft Graph delegated (user) OAuth credentials."""

microsoft_graph_security_service_oauth_secret = RegistryOAuthSecret(
    provider_id="microsoft_graph_security",
    grant_type="client_credentials",
    optional=True,
)
"""Microsoft Graph Security application OAuth credentials."""

microsoft_graph_security_user_oauth_secret = RegistryOAuthSecret(
    provider_id="microsoft_graph_security",
    grant_type="authorization_code",
    optional=True,
)
"""Microsoft Graph Security delegated OAuth credentials."""

microsoft_outlook_service_oauth_secret = RegistryOAuthSecret(
    provider_id="microsoft_outlook",
    grant_type="client_credentials",
    optional=True,
)
"""Microsoft Outlook Mail application OAuth credentials."""

microsoft_outlook_user_oauth_secret = RegistryOAuthSecret(
    provider_id="microsoft_outlook",
    grant_type="authorization_code",
    optional=True,
)
"""Microsoft Outlook Mail delegated OAuth credentials."""

MICROSOFT_GRAPH_SDK_SECRETS: list[RegistrySecretType] = [
    microsoft_graph_service_oauth_secret,
    microsoft_graph_user_oauth_secret,
    microsoft_graph_security_service_oauth_secret,
    microsoft_graph_security_user_oauth_secret,
    microsoft_outlook_service_oauth_secret,
    microsoft_outlook_user_oauth_secret,
]

MICROSOFT_GRAPH = GraphProduct(
    label="Microsoft Graph",
    namespace="tools.microsoft_graph_sdk",
    service_token_names=(microsoft_graph_service_oauth_secret.token_name,),
    user_token_names=(microsoft_graph_user_oauth_secret.token_name,),
)

MICROSOFT_GRAPH_SECURITY = GraphProduct(
    label="Microsoft Graph Security",
    namespace="tools.microsoft_graph_sdk",
    service_token_names=(
        microsoft_graph_security_service_oauth_secret.token_name,
        microsoft_graph_service_oauth_secret.token_name,
    ),
    user_token_names=(
        microsoft_graph_security_user_oauth_secret.token_name,
        microsoft_graph_user_oauth_secret.token_name,
    ),
)

MICROSOFT_OUTLOOK = GraphProduct(
    label="Microsoft Outlook Mail",
    namespace="tools.microsoft_graph_sdk",
    service_token_names=(
        microsoft_outlook_service_oauth_secret.token_name,
        microsoft_graph_service_oauth_secret.token_name,
    ),
    user_token_names=(
        microsoft_outlook_user_oauth_secret.token_name,
        microsoft_graph_user_oauth_secret.token_name,
    ),
)

type MicrosoftGraphOAuthProvider = Literal[
    "microsoft_graph", "microsoft_graph_security", "microsoft_outlook"
]

OAuthProviderParam = Annotated[
    MicrosoftGraphOAuthProvider,
    Field(
        ...,
        description=(
            "OAuth provider to use. Product providers prefer their own token and "
            "fall back to the matching generic Microsoft Graph token. Security and "
            "Outlook credentials are never used as fallbacks for each other."
        ),
    ),
]

GRAPH_PRODUCTS: dict[MicrosoftGraphOAuthProvider, GraphProduct] = {
    "microsoft_graph": MICROSOFT_GRAPH,
    "microsoft_graph_security": MICROSOFT_GRAPH_SECURITY,
    "microsoft_outlook": MICROSOFT_OUTLOOK,
}

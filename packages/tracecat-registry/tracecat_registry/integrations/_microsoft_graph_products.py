"""Credential declarations shared by the Microsoft Graph SDK namespaces.

This module registers no actions. Public SDK modules import these neutral
declarations so importing a product wrapper never registers another namespace as
a side effect.
"""

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

MICROSOFT_GRAPH_SDK_SECRETS: list[RegistrySecretType] = [
    microsoft_graph_service_oauth_secret,
    microsoft_graph_user_oauth_secret,
]

MICROSOFT_GRAPH = GraphProduct(
    label="Microsoft Graph",
    namespace="tools.microsoft_graph_sdk",
    service_token_names=(microsoft_graph_service_oauth_secret.token_name,),
    user_token_names=(microsoft_graph_user_oauth_secret.token_name,),
)

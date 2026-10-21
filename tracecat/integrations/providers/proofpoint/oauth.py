"""Proofpoint Threat Protection OAuth provider (client credentials)."""

from typing import ClassVar

from tracecat.integrations.providers.base import ClientCredentialsOAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

PROOFPOINT_API_DOCS_URL = (
    "https://help.proofpoint.com/Threat_Insight_Dashboard/API_Documentation"
)
PROOFPOINT_SETUP_GUIDE_URL = (
    "https://help.proofpoint.com/Threat_Insight_Dashboard/API_Documentation/Reports_API"
)
PROOFPOINT_TOKEN_ENDPOINT = "https://auth.proofpoint.com/v1/token"


class ProofpointThreatProtectionOAuthProvider(ClientCredentialsOAuthProvider):
    """Proofpoint Threat Protection API key using the client credentials grant."""

    id: ClassVar[str] = "proofpoint_threat_protection"
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="proofpoint_threat_protection",
        name="Proofpoint Threat Protection",
        description=(
            "Proofpoint Threat Protection API key (client credentials) for the "
            "Threat Insight Dashboard Reports and Email Bomb Events APIs."
        ),
        requires_config=True,
        enabled=True,
        api_docs_url=PROOFPOINT_API_DOCS_URL,
        setup_guide_url=PROOFPOINT_SETUP_GUIDE_URL,
        troubleshooting_url=PROOFPOINT_SETUP_GUIDE_URL,
    )
    # Proofpoint only implements the client credentials grant, so there is no
    # consent URL. The base provider requires both endpoints, so this mirrors
    # the token endpoint and is never used to start a consent flow.
    default_authorization_endpoint: ClassVar[str | None] = PROOFPOINT_TOKEN_ENDPOINT
    default_token_endpoint: ClassVar[str | None] = PROOFPOINT_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | list[str] | None] = [
        "Unused. Proofpoint only supports the client credentials grant, so this "
        "endpoint is never called; leave it matching the token endpoint.",
    ]
    token_endpoint_help: ClassVar[str | list[str] | None] = [
        "Use https://auth.proofpoint.com/v1/token.",
        "\n",
        "Create the client ID and secret in the Proofpoint Admin Portal under "
        "Settings > API Key Management > Threat Protection.",
    ]

    def _get_token_endpoint_auth_method(self) -> str | None:
        # Proofpoint expects client_id and client_secret in the form body.
        return "client_secret_post"

"""Palo Alto Networks Strata Cloud Manager OAuth provider (client credentials)."""

from typing import ClassVar

from tracecat.integrations.providers.base import ClientCredentialsOAuthProvider
from tracecat.integrations.schemas import ProviderMetadata, ProviderScopes

SCM_API_DOCS_URL = "https://pan.dev/scm/api/config/ngfw/"
SCM_SETUP_GUIDE_URL = "https://pan.dev/scm/docs/getstarted/"
SCM_TOKEN_ENDPOINT = "https://auth.apps.paloaltonetworks.com/oauth2/access_token"


class PaloAltoSCMOAuthProvider(ClientCredentialsOAuthProvider):
    """Strata Cloud Manager service account using the client credentials grant."""

    id: ClassVar[str] = "pan_strata"
    # SCM scopes every token to a tenant service group (TSG), so the scope is
    # tenant-specific and must be set per integration: tsg_id:<TSG_ID>.
    scopes: ClassVar[ProviderScopes] = ProviderScopes(default=[])
    metadata: ClassVar[ProviderMetadata] = ProviderMetadata(
        id="pan_strata",
        name="Palo Alto Networks (Strata Cloud Manager)",
        description=(
            "Strata Cloud Manager service account (client credentials) for SCM "
            "configuration, operations, and incidents APIs. Supports commercial "
            "and FedRAMP tenants through configurable endpoints."
        ),
        requires_config=True,
        enabled=True,
        api_docs_url=SCM_API_DOCS_URL,
        setup_guide_url=SCM_SETUP_GUIDE_URL,
        troubleshooting_url=SCM_SETUP_GUIDE_URL,
    )
    # SCM only implements the client credentials grant, so there is no consent
    # URL. The base provider requires both endpoints, so this mirrors the token
    # endpoint and is never used to start a consent flow.
    default_authorization_endpoint: ClassVar[str | None] = SCM_TOKEN_ENDPOINT
    default_token_endpoint: ClassVar[str | None] = SCM_TOKEN_ENDPOINT
    authorization_endpoint_help: ClassVar[str | list[str] | None] = [
        "Unused. Strata Cloud Manager only supports the client credentials grant, "
        "so this endpoint is never called; leave it matching the token endpoint.",
    ]
    token_endpoint_help: ClassVar[str | list[str] | None] = [
        "Commercial tenants: https://auth.apps.paloaltonetworks.com/oauth2/access_token",
        "\n",
        "FedRAMP tenants: replace this with the OAuth token URL that Palo Alto "
        "Networks provides for your FedRAMP Strata Cloud Manager tenant, and set "
        "the workspace variable pan_strata.base_url to the matching API host.",
        "\n",
        "Set the scope to tsg_id:<TSG_ID> for the tenant service group that the "
        "service account belongs to.",
    ]

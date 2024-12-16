"""Microsoft Graph authentication via MSAL Python library.

Currently supports confidential app-only authentication (i.e. `acquire_token_for_client` method)

Docs:
- https://learn.microsoft.com/en-us/graph/auth-v2-service
- https://msal-python.readthedocs.io/en/latest/#confidentialclientapplication
- https://learn.microsoft.com/en-us/entra/msal/python/getting-started/acquiring-tokens#confidential-clients-interactive-token-acquisition
"""

from msal import ConfidentialClientApplication
from tracecat import __version__

from tracecat_registry import RegistrySecret, registry, secrets

microsoft_graph_secret = RegistrySecret(
    name="microsoft_graph",
    keys=[
        "MICROSOFT_GRAPH_CLIENT_ID",
        "MICROSOFT_GRAPH_CLIENT_SECRET",
    ],
    optional_keys=[
        "MICROSOFT_GRAPH_SCOPES",
        "MICROSOFT_TOKEN_AUTHORITY",
        "MICROSOFT_OIDC_AUTHORITY",
    ],
)
"""Microsoft Graph API secret.

- name: `microsoft_graph`
- keys:
    - `MICROSOFT_GRAPH_CLIENT_ID`
    - `MICROSOFT_GRAPH_CLIENT_SECRET`
- optional_keys:
    - `MICROSOFT_GRAPH_SCOPES` (comma-separated list of scopes)
    - `MICROSOFT_TOKEN_AUTHORITY`
    - `MICROSOFT_OIDC_AUTHORITY`

Note:
- `MICROSOFT_GRAPH_SCOPES` defaults to `https://graph.microsoft.com/.default`
- `MICROSOFT_TOKEN_AUTHORITY` defaults to `https://login.microsoftonline.com/common`
"""


@registry.register(
    default_title="Get Microsoft Graph auth token",
    description="Get an auth token for Microsoft Graph API calls from a confidential application.",
    display_group="Microsoft Graph",
    namespace="integrations.microsoft_graph",
    secrets=[microsoft_graph_secret],
)
def get_auth_token() -> str:
    client_id = secrets.get("MICROSOFT_GRAPH_CLIENT_ID")
    client_secret = secrets.get("MICROSOFT_GRAPH_CLIENT_SECRET")
    scopes = secrets.get(
        "MICROSOFT_GRAPH_SCOPES", "https://graph.microsoft.com/.default"
    )
    authority = secrets.get(
        "MICROSOFT_TOKEN_AUTHORITY", "https://login.microsoftonline.com/common"
    )
    oidc_authority = secrets.get("MICROSOFT_OIDC_AUTHORITY")
    app = ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=authority,
        oidc_authority=oidc_authority,
        app_name="tracecat",
        app_version=__version__,
    )
    result = app.acquire_token_for_client(scopes=scopes.split(","))
    if result is None:
        raise ValueError("Failed to acquire token. Empty result returned.")
    elif "access_token" in result:
        return result["access_token"]
    else:
        raise ValueError(f"Failed to acquire token: {result}")

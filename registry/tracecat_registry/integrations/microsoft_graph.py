"""Microsoft Graph authentication via MSAL Python library.

Currently supports confidential app-only authentication (i.e. `acquire_token_for_client` method)
"""

from typing import Annotated

from msal import ConfidentialClientApplication
from pydantic import Field
from tracecat import __version__

from tracecat_registry import RegistrySecret, registry, secrets

microsoft_graph_secret = RegistrySecret(
    name="microsoft_graph",
    keys=[
        "MICROSOFT_GRAPH_CLIENT_ID",
        "MICROSOFT_GRAPH_CLIENT_SECRET",
    ],
)
"""Microsoft Graph OAuth2.0 credentials.

- name: `microsoft_graph`
- keys:
    - `MICROSOFT_GRAPH_CLIENT_ID`
    - `MICROSOFT_GRAPH_CLIENT_SECRET`
"""


@registry.register(
    default_title="Get access token",
    description="Retrieve a JWT token for Microsoft Graph API calls from a confidential application.",
    display_group="Microsoft Graph",
    doc_url="https://msal-python.readthedocs.io/en/latest/#confidentialclientapplication",
    namespace="tools.microsoft_graph",
    secrets=[microsoft_graph_secret],
)
def get_access_token(
    scopes: Annotated[
        list[str] | None,
        Field(
            ...,
            description='Microsoft Graph scopes, defaults to ["https://graph.microsoft.com/.default"].',
        ),
    ] = None,
    authority: Annotated[
        str | None,
        Field(
            ...,
            description='Microsoft Graph authority, defaults to "https://login.microsoftonline.com/common".',
        ),
    ] = None,
    oidc_authority: Annotated[
        str | None,
        Field(
            ...,
            description='Microsoft Graph OIDC authority, defaults to "https://login.microsoftonline.com/common".',
        ),
    ] = None,
) -> str:
    client_id = secrets.get("MICROSOFT_GRAPH_CLIENT_ID")
    client_secret = secrets.get("MICROSOFT_GRAPH_CLIENT_SECRET")
    scopes = scopes or ["https://graph.microsoft.com/.default"]
    authority = authority or "https://login.microsoftonline.com/common"
    app = ConfidentialClientApplication(
        client_id=client_id,
        client_credential=client_secret,
        authority=authority,
        oidc_authority=oidc_authority,
        app_name="tracecat",
        app_version=__version__,
    )
    result = app.acquire_token_for_client(scopes=scopes)
    if result is None:
        raise ValueError("Failed to acquire token. Empty result returned.")
    elif "access_token" in result:
        return result["access_token"]
    else:
        raise ValueError(f"Failed to acquire token: {result}")

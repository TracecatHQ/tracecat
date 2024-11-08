"""Microsoft Graph authentication.

Currently supports app-only authentication.

Docs: https://learn.microsoft.com/en-us/graph/auth-v2-service
"""

import httpx

from tracecat_registry import RegistrySecret, registry, secrets

microsoft_graph_secret = RegistrySecret(
    name="microsoft_graph",
    keys=[
        "MICROSOFT_GRAPH_TENANT_ID",
        "MICROSOFT_GRAPH_CLIENT_ID",
        "MICROSOFT_GRAPH_CLIENT_SECRET",
    ],
    optional_keys=["MICROSOFT_GRAPH_SCOPE"],
)
"""Microsoft Graph API secret.

- name: `microsoft_graph`
- keys:
    - `MICROSOFT_GRAPH_TENANT_ID`
    - `MICROSOFT_GRAPH_CLIENT_ID`
    - `MICROSOFT_GRAPH_CLIENT_SECRET`
- optional_keys:
    - `MICROSOFT_GRAPH_SCOPE` (default: `https://graph.microsoft.com/.default`)
"""


@registry.register(
    default_title="Get Microsoft Graph auth token",
    description="Get an auth token for Microsoft Graph API calls.",
    display_group="Microsoft Graph",
    namespace="integrations.microsoft_graph",
    secrets=[microsoft_graph_secret],
)
async def get_auth_token() -> str:
    secret = await secrets.get("microsoft_graph")
    auth_url = f"https://login.microsoftonline.com/{secret['MICROSOFT_GRAPH_TENANT_ID']}/oauth2/v2.0/token"
    async with httpx.AsyncClient() as client:
        response = await client.post(
            auth_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_id": secret["MICROSOFT_GRAPH_CLIENT_ID"],
                "client_secret": secret["MICROSOFT_GRAPH_CLIENT_SECRET"],
                "scope": secret.get(
                    "MICROSOFT_GRAPH_SCOPE", "https://graph.microsoft.com/.default"
                ),
                "grant_type": "client_credentials",
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]

"""Jamf Pro Authentication."""

import httpx

from tracecat_registry import RegistrySecret, registry, secrets

jamf_secret = RegistrySecret(
    name="jamf",
    keys=["JAMF_CLIENT_ID", "JAMF_CLIENT_SECRET"],
)
"""Jamf OAuth2.0 credentials.

- name: `jamf`
- keys:
    - `JAMF_CLIENT_ID`
    - `JAMF_CLIENT_SECRET`
"""


@registry.register(
    default_title="Get access token",
    description="Retrieve a bearer token for Jamf Pro API calls.",
    display_group="Jamf",
    doc_url="https://developer.jamf.com/jamf-pro/docs/jamf-pro-api-overview#authentication-and-authorization",
    namespace="tools.jamf",
    secrets=[jamf_secret],
)
async def get_access_token(base_url: str) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/api/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_id": secrets.get("JAMF_CLIENT_ID"),
                "client_secret": secrets.get("JAMF_CLIENT_SECRET"),
                "grant_type": "client_credentials",
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]

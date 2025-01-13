"""Jamf Pro Authentication.

Docs: https://developer.jamf.com/jamf-pro/docs/client-credentials
"""

import httpx

from tracecat_registry import RegistrySecret, registry, secrets

jamf_secret = RegistrySecret(
    name="jamf",
    keys=["JAMF_BASE_URL", "JAMF_CLIENT_ID", "JAMF_CLIENT_SECRET"],
)
"""Jamf secret.

- name: `jamf`
- keys:
    - `JAMF_BASE_URL`
    - `JAMF_CLIENT_ID`
    - `JAMF_CLIENT_SECRET`
"""


@registry.register(
    default_title="Get Jamf Pro auth token",
    description="Get an auth token for Jamf Pro API calls.",
    display_group="Jamf",
    namespace="integrations.jamf",
    secrets=[jamf_secret],
)
async def get_auth_token() -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{secrets.get('JAMF_BASE_URL')}/api/oauth/token",
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "client_id": secrets.get("JAMF_CLIENT_ID"),
                "client_secret": secrets.get("JAMF_CLIENT_SECRET"),
                "grant_type": "client_credentials",
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]

"""Check Point authentication."""

import httpx

from tracecat_registry import RegistrySecret, registry, secrets

checkpoint_secret = RegistrySecret(
    name="checkpoint",
    keys=[
        "CHECKPOINT_AUTH_URL",
        "CHECKPOINT_CLIENT_ID",
        "CHECKPOINT_ACCESS_KEY",
    ],
)
"""Check Point JWT secret to request an access token.

- name: `checkpoint`
- keys:
    - `CHECKPOINT_AUTH_URL`
    - `CHECKPOINT_CLIENT_ID`
    - `CHECKPOINT_ACCESS_KEY`
"""


@registry.register(
    default_title="Get Check Point auth token",
    description="Get an auth token for Check Point API calls.",
    display_group="Check Point",
    namespace="integrations.check_point",
    secrets=[checkpoint_secret],
)
async def get_auth_token() -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            secrets.get("CHECKPOINT_AUTH_URL"),
            headers={"Content-Type": "application/json"},
            json={
                "clientId": secrets.get("CHECKPOINT_CLIENT_ID"),
                "accessKey": secrets.get("CHECKPOINT_ACCESS_KEY"),
            },
        )
        response.raise_for_status()
        return response.json()["data"]["token"]
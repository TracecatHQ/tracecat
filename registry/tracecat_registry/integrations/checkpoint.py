"""Checkpoint authentication."""

import httpx

from tracecat_registry import RegistrySecret, registry, secrets

checkpoint_secret = RegistrySecret(
    name="checkpoint",
    keys=[
        "CHECKPOINT_AUTH_URL",
        "CHECKPOINT_API_URL",
        "CHECKPOINT_CLIENT_ID",
        "CHECKPOINT_ACCESS_KEY",
    ],
)
"""Checkpoint JWT secret to request an access token.

- name: `checkpoint`
- keys:
    - `CHECKPOINT_AUTH_URL`
    - `CHECKPOINT_API_URL`
    - `CHECKPOINT_CLIENT_ID`
    - `CHECKPOINT_ACCESS_KEY`
"""


@registry.register(
    default_title="Get Checkpoint auth token",
    description="Get an auth token for Checkpoint API calls.",
    display_group="Checkpoint",
    namespace="integrations.checkpoint",
    secrets=[checkpoint_secret],
)
async def get_auth_token() -> str:
    secret = await secrets.get("checkpoint")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            secret["CHECKPOINT_AUTH_URL"],
            headers={"Content-Type": "application/json"},
            json={
                "clientId": secret["CHECKPOINT_CLIENT_ID"],
                "accessKey": secret["CHECKPOINT_ACCESS_KEY"],
            },
        )
        response.raise_for_status()
        return response.json()["token"]

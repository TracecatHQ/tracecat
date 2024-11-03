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
    default_title="Get Checkpoint JWT Token",
    description="Get a JWT token for Checkpoint API calls.",
    namespace="integrations.checkpoint",
    secrets=[checkpoint_secret],
)
async def get_jwt_token() -> str:
    secret = await secrets.get("checkpoint")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            secret["CHECKPOINT_AUTH_URL"],
            json={
                "clientId": secret["CHECKPOINT_CLIENT_ID"],
                "accessKey": secret["CHECKPOINT_ACCESS_KEY"],
            },
        )
        response.raise_for_status()
        return response.json()["data"]["token"]

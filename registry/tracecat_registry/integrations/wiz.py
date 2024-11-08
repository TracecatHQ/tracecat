"""Wiz authentication.

Docs:
- https://docs.cribl.io/stream/4.5/usecase-wiz-api/
- https://explained.tines.com/en/articles/8623326-wiz-authentication-guide
"""

import httpx

from tracecat_registry import RegistrySecret, registry, secrets

wiz_secret = RegistrySecret(
    name="wiz",
    keys=[
        "WIZ_AUTH_URL",
        "WIZ_API_URL",
        "WIZ_CLIENT_ID",
        "WIZ_CLIENT_SECRET",
    ],
)
"""Wiz API key secret.

- name: `wiz`
- keys:
    - `WIZ_AUTH_URL`
    - `WIZ_API_URL`
    - `WIZ_CLIENT_ID`
    - `WIZ_CLIENT_SECRET`
"""


@registry.register(
    default_title="Get Wiz auth token",
    description="Get an auth token for Wiz API calls.",
    display_group="Wiz",
    namespace="integrations.wiz",
    secrets=[wiz_secret],
)
async def get_auth_token() -> str:
    secret = await secrets.get("wiz")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            secret["WIZ_AUTH_URL"],
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "audience": "wiz-api",
                "client_id": secret["WIZ_CLIENT_ID"],
                "client_secret": secret["WIZ_CLIENT_SECRET"],
                "grant_type": "client_credentials",
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]

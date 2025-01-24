"""Wiz authentication.

Docs:
- https://docs.cribl.io/stream/4.5/usecase-wiz-api/
- https://explained.tines.com/en/articles/8623326-wiz-authentication-guide
"""

from typing import Annotated

import httpx
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

wiz_secret = RegistrySecret(
    name="wiz",
    keys=[
        "WIZ_CLIENT_ID",
        "WIZ_CLIENT_SECRET",
    ],
)
"""Wiz OAuth2.0 credentials.

- name: `wiz`
- keys:
    - `WIZ_CLIENT_ID`
    - `WIZ_CLIENT_SECRET`
"""


@registry.register(
    default_title="Get access token",
    description="Retrieve a JWT token for Wiz GraphQLAPI calls.",
    display_group="Wiz",
    namespace="integrations.wiz",
    secrets=[wiz_secret],
)
async def get_access_token(
    auth_url: Annotated[
        str,
        Field(
            ...,
            description="Wiz authentication URL (e.g. https://auth.app.wiz.io/oauth/token)",
        ),
    ] = "https://auth.app.wiz.io/oauth/token",
) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            auth_url,
            headers={"Content-Type": "application/x-www-form-urlencoded"},
            data={
                "audience": "wiz-api",
                "client_id": secrets.get("WIZ_CLIENT_ID"),
                "client_secret": secrets.get("WIZ_CLIENT_SECRET"),
                "grant_type": "client_credentials",
            },
        )
        response.raise_for_status()
        return response.json()["access_token"]

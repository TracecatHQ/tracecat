"""Check Point Infinity authentication."""

import httpx

from tracecat_registry import RegistrySecret, registry, secrets

check_point_infinity_secret = RegistrySecret(
    name="check_point_infinity",
    keys=[
        "CHECKPOINT_CLIENT_ID",
        "CHECKPOINT_ACCESS_KEY",
    ],
)
"""Check Point Infinity OAuth2.0 credentials.

- name: `check_point_infinity`
- keys:
    - `CHECKPOINT_CLIENT_ID`
    - `CHECKPOINT_ACCESS_KEY`
"""


@registry.register(
    default_title="Get access token",
    description="Retrieve a JWT token for Check Point Infinity API calls.",
    display_group="Check Point Infinity",
    doc_url="https://app.swaggerhub.com/apis-docs/Check-Point/infinity-portal-api/1.0.6#/User%20Control/post_auth_external",
    namespace="integrations.check_point_infinity",
    secrets=[check_point_infinity_secret],
)
async def get_access_token(
    base_url: str = "https://cloudinfra-gw-us.portal.checkpoint.com",
) -> str:
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/auth/external",
            headers={"Content-Type": "application/json"},
            json={
                "clientId": secrets.get("CHECKPOINT_CLIENT_ID"),
                "accessKey": secrets.get("CHECKPOINT_ACCESS_KEY"),
            },
        )
        response.raise_for_status()
        return response.json()["data"]["token"]

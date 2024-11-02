"""Generic interface to Checkpoint endpoints.

Abstracts away JWT authentication logic.
"""

from typing import Annotated, Any

import httpx
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets
from tracecat_registry.integrations._http_types import JSONObjectOrArray, RequestMethods

checkpoint_secret = RegistrySecret(
    name="checkpoint",
    keys=[
        "CHECKPOINT_CLIENT_ID",
        "CHECKPOINT_ACCESS_KEY",
        "CHECKPOINT_API_URL",
        "CHECKPOINT_AUTH_URL",
    ],
)
"""Checkpoint JWT secret to request an access token.

- name: `checkpoint`
- keys:
    - `CHECKPOINT_AUTH_URL` (the URL to call to the retrieve JWT token)
    - `CHECKPOINT_CLIENT_ID`
    - `CHECKPOINT_ACCESS_KEY`
    - `CHECKPOINT_API_URL`
"""


@registry.register(
    default_title="Call Checkpoint API",
    description="Call any Checkpoint API.",
    namespace="integrations.checkpoint",
    secrets=[checkpoint_secret],
)
async def call_checkpoint_api(
    endpoint: Annotated[str, Field(description="API endpoint to call.")],
    headers: Annotated[
        dict[str, str],
        Field(description="HTTP request headers"),
    ] = None,
    payload: Annotated[
        JSONObjectOrArray,
        Field(description="HTTP request payload"),
    ] = None,
    params: Annotated[
        dict[str, Any],
        Field(description="URL query parameters"),
    ] = None,
    form_data: Annotated[
        dict[str, Any],
        Field(description="HTTP form encoded data"),
    ] = None,
    method: Annotated[
        RequestMethods,
        Field(description="HTTP request method"),
    ] = "GET",
) -> dict[str, Any]:
    headers = headers or {}
    payload = payload or {}
    params = params or {}
    form_data = form_data or {}

    # Retrieve JWT token
    secret = await secrets.get("checkpoint")
    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{secret['CHECKPOINT_AUTH_URL']}",
            json={
                "clientId": secret["CHECKPOINT_CLIENT_ID"],
                "accessKey": secret["CHECKPOINT_ACCESS_KEY"],
            },
        )
        response.raise_for_status()
        token = response.json()["data"]["token"]

    headers["Authorization"] = f"Bearer {token}"

    # Make API call
    async with httpx.AsyncClient() as client:
        response = await client.request(
            method,
            f"{secret['CHECKPOINT_API_URL']}/{endpoint}",
            headers=headers,
            params=params,
            json=payload,
            form_data=form_data,
        )
        response.raise_for_status()
        return response.json()

"""Generic interface to Limacharlie endpoints.

Abstracts away JWT authentication logic.

Docs: https://docs.limacharlie.io/apidocs/introduction
"""

from typing import Annotated, Any

import httpx
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets
from tracecat_registry.integrations._http_types import JSONObjectOrArray, RequestMethods

limacharlie_secret = RegistrySecret(
    name="limacharlie",
    keys=["LIMACHARLIE_SECRET", "LIMACHARLIE_UID"],
    optional_keys=["LIMACHARLIE_OID"],
)
"""Limacharlie secret.

- name: `limacharlie`
- keys:
    - `LIMACHARLIE_SECRET`
    - `LIMACHARLIE_UID`
- optional_keys:
    - `LIMACHARLIE_OID` (organization ID)
"""

LIMACHARLIE_API_URL = "https://api.limacharlie.io/v1"
LIMACHARLIE_AUTH_URL = "https://jwt.limacharlie.io"


@registry.register(
    default_title="Call Limacharlie API",
    description="Call any Limacharlie API.",
    namespace="integrations.limacharlie",
    secrets=[limacharlie_secret],
)
async def call_limacharlie_api(
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
    secret = await secrets.get("limacharlie")
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{LIMACHARLIE_AUTH_URL}",
            params={
                "uid": secret["LIMACHARLIE_UID"],
                "secret": secret["LIMACHARLIE_SECRET"],
                "oid": secret.get("LIMACHARLIE_OID", "-"),
            },
        )
        response.raise_for_status()
        token = response.json()["jwt"]

    headers["Authorization"] = f"Bearer {token}"

    async with httpx.AsyncClient() as client:
        response = await client.request(
            method,
            f"{LIMACHARLIE_API_URL}/{endpoint}",
            headers=headers,
            params=params,
            json=payload,
            form_data=form_data,
        )
        response.raise_for_status()
        return response.json()

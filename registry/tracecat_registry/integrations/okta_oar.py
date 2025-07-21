"""Okta OAR REST API integration for governance and requests."""

from typing import Annotated, Any

import httpx
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

okta_secret = RegistrySecret(
    name="okta",
    keys=[
        "OKTA_API_TOKEN",
    ],
)
"""Okta API credentials.
- name: `okta`
- keys:
    - `OAR_API_TOKEN`: Okta API token for authentication
"""


def _get_okta_headers() -> dict[str, str]:
    """Get standard headers for Okta API requests."""
    return {
        "Authorization": f"SSWS {secrets.get('OKTA_API_TOKEN')}",
        "Accept": "application/json",
        "Content-Type": "application/json",
    }


@registry.register(
    default_title="Get requests",
    description="Get Okta Access Request tickets with optional filtering and pagination.",
    display_group="Okta OAR",
    doc_url="https://developer.okta.com/docs/api/iga/openapi/governance.requests.admin.v1/tag/Requests/",
    namespace="tools.okta_oar",
    secrets=[okta_secret],
)
async def get_requests(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    after: Annotated[
        str,
        Field("", description="Pagination cursor for retrieving next set of results"),
    ] = "",
    filter: Annotated[
        str,
        Field("", description="Filter expression for requests"),
    ] = "",
    limit: Annotated[
        str,
        Field("20", description="Number of requests to return (default: 20)"),
    ] = "20",
    order_by: Annotated[
        str,
        Field("", description="Field to order results by"),
    ] = "",
) -> dict[str, Any]:
    """Get Okta Access Request tickets."""
    headers = _get_okta_headers()

    params: dict[str, str] = {}
    if after:
        params["after"] = after
    if filter:
        params["filter"] = filter
    if limit:
        params["limit"] = limit
    if order_by:
        params["orderBy"] = order_by

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/governance/api/v1/requests",
            headers=headers,
            params=params,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get specific request",
    description="Get a specific Okta Access Request ticket by ID.",
    display_group="Okta OAR",
    doc_url="https://developer.okta.com/docs/api/iga/openapi/governance.requests.admin.v1/tag/Requests/#tag/Requests/operation/getRequest",
    namespace="tools.okta_oar",
    secrets=[okta_secret],
)
async def get_specific_request(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    request_id: Annotated[
        str,
        Field(..., description="ID of the access request to retrieve"),
    ],
) -> dict[str, Any]:
    """Get a specific Okta Access Request ticket by ID."""
    headers = _get_okta_headers()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/governance/api/v1/requests/{request_id}",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get user",
    description="Get an Okta user by ID.",
    display_group="Okta OAR",
    doc_url="https://developer.okta.com/docs/reference/api/users/#get-user",
    namespace="tools.okta_oar",
    secrets=[okta_secret],
)
async def get_user(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    user_id: Annotated[
        str,
        Field(..., description="User ID, login, or email of the user to retrieve"),
    ],
) -> dict[str, Any]:
    """Get an Okta user by ID."""
    headers = _get_okta_headers()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"{base_url}/api/v1/users/{user_id}",
            headers=headers,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Create message",
    description="Create a message in an Okta Access Request ticket.",
    display_group="Okta OAR",
    doc_url="https://developer.okta.com/docs/api/iga/openapi/governance.requests.admin.v1/tag/Requests/#tag/Requests/operation/createRequestMessage",
    namespace="tools.okta_oar",
    secrets=[okta_secret],
)
async def create_message(
    base_url: Annotated[
        str,
        Field(
            ..., description="Okta domain base URL (e.g., 'https://dev-12345.okta.com')"
        ),
    ],
    request_id: Annotated[
        str,
        Field(..., description="ID of the access request to add message to"),
    ],
    message: Annotated[
        str,
        Field(..., description="Message content to add to the request"),
    ],
) -> dict[str, Any]:
    """Create a message in an Okta Access Request ticket."""
    headers = _get_okta_headers()

    payload = {"message": message}

    async with httpx.AsyncClient() as client:
        response = await client.post(
            f"{base_url}/governance/api/v1/requests/{request_id}/messages",
            headers=headers,
            json=payload,
        )
        response.raise_for_status()

        # Handle empty response (successful creation)
        try:
            result = response.json()
            if not result:  # Empty dict/response means success
                return {"message": "Message created successfully"}
            return result
        except Exception:
            # If JSON parsing fails but status is good, assume success
            return {"message": "Message created successfully"}

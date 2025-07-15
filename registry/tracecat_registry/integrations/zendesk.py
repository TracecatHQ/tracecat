"""Zendesk API integration for ticket management and operations."""

import base64
from typing import Annotated, Any, Literal

import httpx
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

zendesk_secret = RegistrySecret(
    name="zendesk", keys=["ZENDESK_EMAIL", "ZENDESK_API_TOKEN"]
)
"""Zendesk API credentials.

- name: `zendesk`
- keys:
    - `ZENDESK_EMAIL`: Email address of the API user
    - `ZENDESK_API_TOKEN`: API token for authentication
"""


def _get_zendesk_client(subdomain: str) -> tuple[httpx.AsyncClient, str]:
    """Create authenticated Zendesk HTTP client."""
    email = secrets.get("ZENDESK_EMAIL")
    api_token = secrets.get("ZENDESK_API_TOKEN")

    base_url = f"https://{subdomain}.zendesk.com/api/v2"

    # Create basic auth header
    auth_string = f"{email}/token:{api_token}"
    auth_bytes = auth_string.encode("ascii")
    auth_b64 = base64.b64encode(auth_bytes).decode("ascii")

    headers = {
        "Authorization": f"Basic {auth_b64}",
        "Content-Type": "application/json",
        "Accept": "application/json",
    }

    client = httpx.AsyncClient(base_url=base_url, headers=headers)
    return client, base_url


@registry.register(
    default_title="Get ticket",
    description="Retrieve a specific ticket by ID from Zendesk.",
    doc_url="https://developer.zendesk.com/api-reference/ticketing/tickets/tickets/#show-ticket",
    display_group="Zendesk",
    namespace="integrations.zendesk",
    secrets=[zendesk_secret],
)
async def get_ticket(
    subdomain: Annotated[
        str,
        Field(
            ...,
            description="Your Zendesk subdomain (e.g., 'company' for company.zendesk.com)",
        ),
    ],
    ticket_id: Annotated[
        int, Field(..., description="The ID of the ticket to retrieve")
    ],
) -> dict[str, Any]:
    """Get a specific ticket by ID."""
    client, _ = _get_zendesk_client(subdomain)

    async with client:
        response = await client.get(f"/tickets/{ticket_id}.json")
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Search Zendesk",
    description="Search Zendesk using search query syntax.",
    doc_url="https://developer.zendesk.com/api-reference/ticketing/ticket-management/search/#list-search-results",
    display_group="Zendesk",
    namespace="integrations.zendesk",
    secrets=[zendesk_secret],
)
async def search_tickets(
    subdomain: Annotated[
        str,
        Field(
            ...,
            description="Your Zendesk subdomain (e.g., 'company' for company.zendesk.com)",
        ),
    ],
    query: Annotated[
        str,
        Field(
            ...,
            description="Search query using Zendesk search syntax. Default: type:ticket -status:solved -status:closed",
        ),
    ] = "type:ticket -status:solved -status:closed",
    sort_by: Annotated[
        Literal["created_at", "updated_at", "priority", "status", "ticket_type"] | None,
        Field(None, description="Field to sort results by"),
    ] = None,
    sort_order: Annotated[
        Literal["asc", "desc"] | None, Field(None, description="Sort order")
    ] = None,
    page: Annotated[
        int | None, Field(None, description="Page number for pagination (1-based)")
    ] = None,
    per_page: Annotated[
        int, Field(100, description="Number of results per page (max 100)")
    ] = 100,
) -> dict[str, Any]:
    """Search for tickets using query syntax."""
    client, _ = _get_zendesk_client(subdomain)

    params = {"query": query, "per_page": min(per_page, 100)}
    if sort_by:
        params["sort_by"] = sort_by
    if sort_order:
        params["sort_order"] = sort_order
    if page:
        params["page"] = page

    async with client:
        response = await client.get("/search.json", params=params)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get ticket comments",
    description="Retrieve all comments for a specific ticket.",
    display_group="Zendesk",
    namespace="integrations.zendesk",
    secrets=[zendesk_secret],
)
async def get_ticket_comments(
    subdomain: Annotated[
        str,
        Field(
            ...,
            description="Your Zendesk subdomain (e.g., 'company' for company.zendesk.com)",
        ),
    ],
    ticket_id: Annotated[int, Field(..., description="The ID of the ticket")],
    page: Annotated[
        int | None, Field(None, description="Page number for pagination (1-based)")
    ] = None,
    per_page: Annotated[
        int, Field(100, description="Number of comments per page (max 100)")
    ] = 100,
) -> dict[str, Any]:
    """Get all comments for a ticket."""
    client, _ = _get_zendesk_client(subdomain)

    params = {"per_page": min(per_page, 100)}
    if page:
        params["page"] = page

    async with client:
        response = await client.get(
            f"/tickets/{ticket_id}/comments.json", params=params
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get ticket attachments",
    description="Retrieve attachments from a specific ticket.",
    display_group="Zendesk",
    namespace="integrations.zendesk",
    secrets=[zendesk_secret],
)
async def get_ticket_attachments(
    subdomain: Annotated[
        str,
        Field(
            ...,
            description="Your Zendesk subdomain (e.g., 'company' for company.zendesk.com)",
        ),
    ],
    ticket_id: Annotated[int, Field(..., description="The ID of the ticket")],
) -> dict[str, Any]:
    """Get attachments from a ticket by retrieving ticket comments and extracting attachments."""
    client, _ = _get_zendesk_client(subdomain)

    async with client:
        # Get ticket comments which contain attachment information
        response = await client.get(f"/tickets/{ticket_id}/comments.json")
        response.raise_for_status()
        comments_data = response.json()

        # Extract attachments from comments
        attachments = []
        for comment in comments_data.get("comments", []):
            if comment.get("attachments"):
                for attachment in comment["attachments"]:
                    attachments.append(
                        {"comment_id": comment["id"], "attachment": attachment}
                    )

        return {"ticket_id": ticket_id, "attachments": attachments}


@registry.register(
    default_title="Get groups",
    description="Retrieve all groups from Zendesk.",
    display_group="Zendesk",
    namespace="integrations.zendesk",
    secrets=[zendesk_secret],
)
async def get_groups(
    subdomain: Annotated[
        str,
        Field(
            ...,
            description="Your Zendesk subdomain (e.g., 'company' for company.zendesk.com)",
        ),
    ],
    page: Annotated[
        int | None, Field(None, description="Page number for pagination (1-based)")
    ] = None,
    per_page: Annotated[
        int, Field(100, description="Number of groups per page (max 100)")
    ] = 100,
) -> dict[str, Any]:
    """Get all groups."""
    client, _ = _get_zendesk_client(subdomain)

    params = {"per_page": min(per_page, 100)}
    if page:
        params["page"] = page

    async with client:
        response = await client.get("/groups.json", params=params)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get group users",
    description="Retrieve all users in a specific group.",
    display_group="Zendesk",
    namespace="integrations.zendesk",
    secrets=[zendesk_secret],
)
async def get_group_users(
    subdomain: Annotated[
        str,
        Field(
            ...,
            description="Your Zendesk subdomain (e.g., 'company' for company.zendesk.com)",
        ),
    ],
    group_id: Annotated[int, Field(..., description="The ID of the group")],
    page: Annotated[
        int | None, Field(None, description="Page number for pagination (1-based)")
    ] = None,
    per_page: Annotated[
        int, Field(100, description="Number of users per page (max 100)")
    ] = 100,
) -> dict[str, Any]:
    """Get all users in a specific group."""
    client, _ = _get_zendesk_client(subdomain)

    params = {"per_page": min(per_page, 100)}
    if page:
        params["page"] = page

    async with client:
        response = await client.get(f"/groups/{group_id}/users.json", params=params)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get Twilio recordings",
    description="Retrieve Twilio call recordings associated with tickets (requires Twilio integration).",
    display_group="Zendesk",
    namespace="integrations.zendesk",
    secrets=[zendesk_secret],
)
async def get_twilio_recordings(
    subdomain: Annotated[
        str,
        Field(
            ...,
            description="Your Zendesk subdomain (e.g., 'company' for company.zendesk.com)",
        ),
    ],
    recording_id: Annotated[str, Field(..., description="Recording ID")],
) -> dict[str, Any]:
    """Get Twilio recordings. Note: This requires Twilio integration to be configured in Zendesk."""
    client, _ = _get_zendesk_client(subdomain)

    async with client:
        # This endpoint may vary depending on how Twilio integration is set up
        # This is a generic approach - actual endpoint may need adjustment
        try:
            response = await client.get(
                f"channels/voice/calls/{recording_id}/twilio/call/recording",
            )
            response.raise_for_status()

            # Check if response is binary (audio file) or JSON
            content_type = response.headers.get("content-type", "")
            if "audio" in content_type or "application/octet-stream" in content_type:
                # Return binary data as base64 encoded string
                import base64

                return {
                    "recording_id": recording_id,
                    "content_type": content_type,
                    "data": base64.b64encode(response.content).decode("utf-8"),
                    "size": len(response.content),
                }
            else:
                # Assume JSON response
                return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {
                    "error": "File Not found or Twilio integration not configured",
                    "recordings": [],
                    "message": "This endpoint requires Twilio integration to be set up in Zendesk",
                }
            raise
        except Exception as e:
            return {
                "error": str(e),
                "recordings": [],
                "message": "This endpoint requires Twilio integration to be set up in Zendesk",
            }

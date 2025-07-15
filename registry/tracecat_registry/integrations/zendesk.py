"""Zendesk API integration for ticket management and operations."""

import base64
from typing import Annotated, Any, Literal

import httpx
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

zendesk_secret = RegistrySecret(
    name="zendesk", keys=["ZENDESK_SUBDOMAIN", "ZENDESK_EMAIL", "ZENDESK_API_TOKEN"]
)
"""Zendesk API credentials.

- name: `zendesk`
- keys:
    - `ZENDESK_SUBDOMAIN`: Your Zendesk subdomain (e.g., 'company' for company.zendesk.com)
    - `ZENDESK_EMAIL`: Email address of the API user
    - `ZENDESK_API_TOKEN`: API token for authentication
"""


def _get_zendesk_client() -> tuple[httpx.AsyncClient, str]:
    """Create authenticated Zendesk HTTP client."""
    subdomain = secrets.get("ZENDESK_SUBDOMAIN")
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
    ticket_id: Annotated[
        int, Field(..., description="The ID of the ticket to retrieve")
    ],
) -> dict[str, Any]:
    """Get a specific ticket by ID."""
    client, _ = _get_zendesk_client()

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
    client, _ = _get_zendesk_client()

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
    ticket_id: Annotated[int, Field(..., description="The ID of the ticket")],
    page: Annotated[
        int | None, Field(None, description="Page number for pagination (1-based)")
    ] = None,
    per_page: Annotated[
        int, Field(100, description="Number of comments per page (max 100)")
    ] = 100,
) -> dict[str, Any]:
    """Get all comments for a ticket."""
    client, _ = _get_zendesk_client()

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
    ticket_id: Annotated[int, Field(..., description="The ID of the ticket")],
) -> dict[str, Any]:
    """Get attachments from a ticket by retrieving ticket comments and extracting attachments."""
    client, _ = _get_zendesk_client()

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
    default_title="Add attachment to ticket",
    description="Upload and attach a file to a Zendesk ticket.",
    display_group="Zendesk",
    namespace="integrations.zendesk",
    secrets=[zendesk_secret],
)
async def add_attachment_to_ticket(
    ticket_id: Annotated[int, Field(..., description="The ID of the ticket")],
    file_content: Annotated[str, Field(..., description="Base64 encoded file content")],
    filename: Annotated[str, Field(..., description="Name of the file")],
    content_type: Annotated[str, Field(..., description="MIME type of the file")],
    comment_body: Annotated[
        str | None, Field(None, description="Comment text to add with the attachment")
    ] = None,
) -> dict[str, Any]:
    """Add an attachment to a ticket by first uploading it, then adding it to a comment."""
    client, _ = _get_zendesk_client()

    async with client:
        # Step 1: Upload the attachment
        files = {"file": (filename, base64.b64decode(file_content), content_type)}

        upload_response = await client.post(
            f"/uploads.json?filename={filename}", files=files
        )
        upload_response.raise_for_status()
        upload_data = upload_response.json()

        # Step 2: Add comment with attachment to ticket
        comment_data = {
            "ticket": {
                "comment": {
                    "body": comment_body or f"Attachment: {filename}",
                    "uploads": [upload_data["upload"]["token"]],
                }
            }
        }

        comment_response = await client.put(
            f"/tickets/{ticket_id}.json", json=comment_data
        )
        comment_response.raise_for_status()

        return {
            "ticket_id": ticket_id,
            "upload": upload_data,
            "comment": comment_response.json(),
        }


@registry.register(
    default_title="Get groups",
    description="Retrieve all groups from Zendesk.",
    display_group="Zendesk",
    namespace="integrations.zendesk",
    secrets=[zendesk_secret],
)
async def get_groups(
    page: Annotated[
        int | None, Field(None, description="Page number for pagination (1-based)")
    ] = None,
    per_page: Annotated[
        int, Field(100, description="Number of groups per page (max 100)")
    ] = 100,
) -> dict[str, Any]:
    """Get all groups."""
    client, _ = _get_zendesk_client()

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
    group_id: Annotated[int, Field(..., description="The ID of the group")],
    page: Annotated[
        int | None, Field(None, description="Page number for pagination (1-based)")
    ] = None,
    per_page: Annotated[
        int, Field(100, description="Number of users per page (max 100)")
    ] = 100,
) -> dict[str, Any]:
    """Get all users in a specific group."""
    client, _ = _get_zendesk_client()

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
    ticket_id: Annotated[
        int | None, Field(None, description="Optional ticket ID to filter recordings")
    ] = None,
    limit: Annotated[
        int, Field(100, description="Maximum number of recordings to retrieve")
    ] = 100,
) -> dict[str, Any]:
    """Get Twilio recordings. Note: This requires Twilio integration to be configured in Zendesk."""
    client, _ = _get_zendesk_client()

    params = {"limit": limit}
    if ticket_id:
        params["ticket_id"] = ticket_id

    async with client:
        # This endpoint may vary depending on how Twilio integration is set up
        # This is a generic approach - actual endpoint may need adjustment
        try:
            response = await client.get(
                "/integrations/twilio/recordings.json", params=params
            )
            response.raise_for_status()
            return response.json()
        except httpx.HTTPStatusError as e:
            if e.response.status_code == 404:
                return {
                    "error": "Twilio integration not found or not configured",
                    "recordings": [],
                    "message": "This endpoint requires Twilio integration to be set up in Zendesk",
                }
            raise


@registry.register(
    default_title="Create ticket",
    description="Create a new ticket in Zendesk.",
    display_group="Zendesk",
    namespace="integrations.zendesk",
    secrets=[zendesk_secret],
)
async def create_ticket(
    subject: Annotated[str, Field(..., description="Ticket subject")],
    description: Annotated[str, Field(..., description="Ticket description/body")],
    requester_email: Annotated[
        str | None, Field(None, description="Email of the requester")
    ] = None,
    priority: Annotated[
        Literal["urgent", "high", "normal", "low"] | None,
        Field(None, description="Ticket priority"),
    ] = None,
    ticket_type: Annotated[
        Literal["problem", "incident", "question", "task"] | None,
        Field(None, description="Type of ticket"),
    ] = None,
    tags: Annotated[
        list[str] | None, Field(None, description="Tags to add to the ticket")
    ] = None,
) -> dict[str, Any]:
    """Create a new ticket."""
    client, _ = _get_zendesk_client()

    ticket_data = {"ticket": {"subject": subject, "comment": {"body": description}}}

    if requester_email:
        ticket_data["ticket"]["requester"] = {"email": requester_email}
    if priority:
        ticket_data["ticket"]["priority"] = priority
    if ticket_type:
        ticket_data["ticket"]["type"] = ticket_type
    if tags:
        ticket_data["ticket"]["tags"] = tags

    async with client:
        response = await client.post("/tickets.json", json=ticket_data)
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Update ticket",
    description="Update an existing ticket in Zendesk.",
    display_group="Zendesk",
    namespace="integrations.zendesk",
    secrets=[zendesk_secret],
)
async def update_ticket(
    ticket_id: Annotated[int, Field(..., description="The ID of the ticket to update")],
    status: Annotated[
        Literal["new", "open", "pending", "hold", "solved", "closed"] | None,
        Field(None, description="New status for the ticket"),
    ] = None,
    priority: Annotated[
        Literal["urgent", "high", "normal", "low"] | None,
        Field(None, description="New priority for the ticket"),
    ] = None,
    comment: Annotated[
        str | None, Field(None, description="Comment to add to the ticket")
    ] = None,
    tags: Annotated[
        list[str] | None, Field(None, description="Tags to add to the ticket")
    ] = None,
) -> dict[str, Any]:
    """Update an existing ticket."""
    client, _ = _get_zendesk_client()

    ticket_data = {"ticket": {}}

    if status:
        ticket_data["ticket"]["status"] = status
    if priority:
        ticket_data["ticket"]["priority"] = priority
    if comment:
        ticket_data["ticket"]["comment"] = {"body": comment}
    if tags:
        ticket_data["ticket"]["tags"] = tags

    async with client:
        response = await client.put(f"/tickets/{ticket_id}.json", json=ticket_data)
        response.raise_for_status()
        return response.json()

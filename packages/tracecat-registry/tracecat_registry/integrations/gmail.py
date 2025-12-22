"""Gmail integration UDFs for Tracecat.

This module provides Gmail API integration for security automation workflows,
including email search, retrieval, and phishing investigation capabilities.

Uses Tracecat's built-in OAuth system for seamless authentication.
Configure the 'google_gmail' OAuth integration in Tracecat UI, then users
can click "Connect with OAuth" to authorize Gmail access.
"""

from typing import Annotated, Any

import httpx
from pydantic import Field

from tracecat_registry import RegistryOAuthSecret, registry, secrets


gmail_oauth_secret = RegistryOAuthSecret(
    provider_id="google_gmail",
    grant_type="authorization_code",
)
"""Gmail OAuth credentials.

Configure the 'google_gmail' OAuth integration in Tracecat with:
- OAuth 2.0 client credentials from Google Cloud Console
- Required scopes: https://www.googleapis.com/auth/gmail.readonly (or gmail.modify for write operations)

See: https://developers.google.com/gmail/api/auth/about-auth
"""


def _get_gmail_token() -> str:
    """Get access token from Tracecat's OAuth system."""
    return secrets.get("GOOGLE_GMAIL_USER_TOKEN")


@registry.register(
    default_title="Search Gmail messages",
    display_group="Gmail",
    description="Search Gmail messages using Gmail's powerful query syntax",
    namespace="tools.gmail",
    secrets=[gmail_oauth_secret],
)
async def search_messages(
    query: Annotated[
        str,
        Field(
            description="Gmail search query (e.g., 'from:suspicious@evil.com has:attachment')"
        ),
    ],
    max_results: Annotated[
        int, Field(default=10, description="Maximum number of results to return")
    ] = 10,
    user_id: Annotated[
        str, Field(default="me", description="User ID or 'me' for authenticated user")
    ] = "me",
    page_token: Annotated[
        str | None, Field(description="Page token for pagination")
    ] = None,
) -> dict[str, Any]:
    """
    Search Gmail messages using Gmail's search syntax.

    Common query operators for security investigations:
    - from:email - Messages from specific sender
    - to:email - Messages to specific recipient
    - subject:text - Messages with text in subject
    - has:attachment - Messages with attachments
    - filename:pdf - Messages with specific attachment type
    - after:YYYY/MM/DD - Messages after date
    - before:YYYY/MM/DD - Messages before date
    - is:unread - Unread messages
    - label:spam - Messages in spam folder

    Example queries:
    - "from:attacker@evil.com" - Find emails from suspicious sender
    - "has:attachment filename:exe" - Find emails with executable attachments
    - "subject:invoice after:2024/01/01" - Find invoice emails since Jan 2024

    Returns the full API response with messages array and nextPageToken.
    """
    access_token = _get_gmail_token()

    params: dict[str, Any] = {"q": query, "maxResults": max_results}
    if page_token:
        params["pageToken"] = page_token

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get Gmail message",
    display_group="Gmail",
    description="Get full content of a Gmail message by ID",
    namespace="tools.gmail",
    secrets=[gmail_oauth_secret],
)
async def get_message(
    message_id: Annotated[str, Field(description="The message ID to retrieve")],
    user_id: Annotated[
        str, Field(default="me", description="User ID or 'me' for authenticated user")
    ] = "me",
    format: Annotated[
        str,
        Field(
            default="full",
            description="Format: 'full' (complete), 'metadata' (headers only), 'minimal' (IDs only), 'raw' (RFC 2822)",
        ),
    ] = "full",
) -> dict[str, Any]:
    """
    Get the full content of a Gmail message.

    Returns the complete message including headers, body, and attachment metadata.
    Useful for deep inspection during phishing investigations.

    Format options:
    - full: Returns the full email message data with body parsed
    - metadata: Returns only headers and metadata (faster)
    - minimal: Returns only IDs and labels
    - raw: Returns the full email as RFC 2822 formatted string
    """
    access_token = _get_gmail_token()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/messages/{message_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"format": format},
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get Gmail message headers",
    display_group="Gmail",
    description="Get only headers of a Gmail message (faster than full message)",
    namespace="tools.gmail",
    secrets=[gmail_oauth_secret],
)
async def get_message_headers(
    message_id: Annotated[
        str, Field(description="The message ID to retrieve headers for")
    ],
    user_id: Annotated[
        str, Field(default="me", description="User ID or 'me' for authenticated user")
    ] = "me",
    metadata_headers: Annotated[
        list[str] | None,
        Field(
            description="Specific headers to return (e.g., ['From', 'To', 'Subject']). If not specified, returns all headers."
        ),
    ] = None,
) -> dict[str, Any]:
    """
    Get only the headers of a Gmail message.

    Faster than getting the full message. Useful for quick inspection
    of email metadata during investigations.

    Returns headers including: From, To, Subject, Date, Message-ID,
    Reply-To, Return-Path, Received, X-headers, etc.
    """
    access_token = _get_gmail_token()

    params: dict[str, Any] = {"format": "metadata"}
    if metadata_headers:
        params["metadataHeaders"] = metadata_headers

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/messages/{message_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params=params,
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="List Gmail labels",
    display_group="Gmail",
    description="List all Gmail labels for the user",
    namespace="tools.gmail",
    secrets=[gmail_oauth_secret],
)
async def list_labels(
    user_id: Annotated[
        str, Field(default="me", description="User ID or 'me' for authenticated user")
    ] = "me",
) -> dict[str, Any]:
    """
    List all Gmail labels for a user.

    Useful for understanding the user's email organization
    and filtering messages by label.
    """
    access_token = _get_gmail_token()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/labels",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get Gmail thread",
    display_group="Gmail",
    description="Get all messages in an email thread",
    namespace="tools.gmail",
    secrets=[gmail_oauth_secret],
)
async def get_thread(
    thread_id: Annotated[str, Field(description="The thread ID to retrieve")],
    user_id: Annotated[
        str, Field(default="me", description="User ID or 'me' for authenticated user")
    ] = "me",
    format: Annotated[
        str,
        Field(
            default="full",
            description="Format: 'full' (complete), 'metadata' (headers only), 'minimal' (IDs only)",
        ),
    ] = "full",
) -> dict[str, Any]:
    """
    Get all messages in an email thread.

    Useful for seeing the full conversation context during investigations.
    Returns all messages in the thread with their content.
    """
    access_token = _get_gmail_token()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/threads/{thread_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"format": format},
        )
        response.raise_for_status()
        return response.json()


@registry.register(
    default_title="Get Gmail attachment",
    display_group="Gmail",
    description="Download an attachment from a Gmail message",
    namespace="tools.gmail",
    secrets=[gmail_oauth_secret],
)
async def get_attachment(
    message_id: Annotated[str, Field(description="The message ID containing the attachment")],
    attachment_id: Annotated[str, Field(description="The attachment ID to retrieve")],
    user_id: Annotated[
        str, Field(default="me", description="User ID or 'me' for authenticated user")
    ] = "me",
) -> dict[str, Any]:
    """
    Download an attachment from a Gmail message.

    Returns the attachment data as base64-encoded string.
    Useful for extracting and analyzing suspicious attachments.
    """
    access_token = _get_gmail_token()

    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/messages/{message_id}/attachments/{attachment_id}",
            headers={"Authorization": f"Bearer {access_token}"},
        )
        response.raise_for_status()
        return response.json()

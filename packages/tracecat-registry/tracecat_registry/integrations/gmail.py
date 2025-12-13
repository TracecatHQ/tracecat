"""Gmail integration UDFs for Tracecat.

This module provides Gmail API integration for security automation workflows,
including email search, retrieval, and phishing investigation capabilities.

Uses Tracecat's built-in OAuth system for seamless authentication.
Configure the 'google_gmail' OAuth integration in Tracecat UI, then users
can click "Connect with OAuth" to authorize Gmail access.
"""

from typing import Annotated, Any
import base64
import httpx
from pydantic import Field
from tracecat_registry import registry, RegistryOAuthSecret, secrets
from tracecat_registry._internal.logger import logger


# Use Tracecat's built-in OAuth system
# Users configure OAuth in Tracecat UI → Connect with OAuth → Done!
gmail_oauth_secret = RegistryOAuthSecret(
    provider_id="google_gmail",
    grant_type="authorization_code",
)


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
    query: Annotated[str, Field(description="Gmail search query (e.g., 'from:suspicious@evil.com has:attachment')")],
    max_results: Annotated[int, Field(default=10, description="Maximum number of results to return")] = 10,
    user_id: Annotated[str, Field(default="me", description="User ID or 'me' for authenticated user")] = "me",
) -> list[dict[str, Any]]:
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
    
    Returns list of message metadata (id, threadId, snippet, etc.)
    """
    access_token = _get_gmail_token()
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/messages",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"q": query, "maxResults": max_results},
        )
        response.raise_for_status()
        data = response.json()
        
        messages = data.get("messages", [])
        
        # Fetch metadata for each message
        results = []
        for msg in messages:
            msg_id = msg['id']
            msg_response = await client.get(
                f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/messages/{msg_id}",
                headers={"Authorization": f"Bearer {access_token}"},
                params={"format": "metadata", "metadataHeaders": ["From", "To", "Subject", "Date"]},
            )
            if msg_response.status_code == 200:
                msg_data = msg_response.json()
                headers = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
                results.append({
                    "id": msg_id,
                    "threadId": msg["threadId"],
                    "snippet": msg_data.get("snippet", ""),
                    "from": headers.get("From", ""),
                    "to": headers.get("To", ""),
                    "subject": headers.get("Subject", ""),
                    "date": headers.get("Date", ""),
                    "labelIds": msg_data.get("labelIds", []),
                })
            else:
                # Log the failure and include error info in results
                error_detail = msg_response.text[:200] if msg_response.text else "No error details"
                logger.warning(
                    f"Failed to fetch Gmail message {msg_id}: HTTP {msg_response.status_code} - {error_detail}"
                )
                results.append({
                    "id": msg_id,
                    "threadId": msg["threadId"],
                    "error": f"Failed to fetch message metadata: HTTP {msg_response.status_code}",
                    "status_code": msg_response.status_code,
                })
        
        return results


@registry.register(
    default_title="Get Gmail message",
    display_group="Gmail",
    description="Get full content of a Gmail message by ID",
    namespace="tools.gmail",
    secrets=[gmail_oauth_secret],
)
async def get_message(
    message_id: Annotated[str, Field(description="The message ID to retrieve")],
    user_id: Annotated[str, Field(default="me", description="User ID or 'me' for authenticated user")] = "me",
) -> dict[str, Any]:
    """
    Get the full content of a Gmail message.
    
    Returns the complete message including headers, body, and attachment metadata.
    Useful for deep inspection during phishing investigations.
    """
    access_token = _get_gmail_token()
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/messages/{message_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"format": "full"},
        )
        response.raise_for_status()
        msg_data = response.json()
        
        # Extract headers
        headers = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
        
        # Extract body
        body_text = ""
        body_html = ""
        payload = msg_data.get("payload", {})
        
        def extract_body(part):
            nonlocal body_text, body_html
            mime_type = part.get("mimeType", "")
            if "data" in part.get("body", {}):
                data = part["body"]["data"]
                decoded = base64.urlsafe_b64decode(data).decode("utf-8", errors="ignore")
                if mime_type == "text/plain":
                    body_text = decoded
                elif mime_type == "text/html":
                    body_html = decoded
            for sub_part in part.get("parts", []):
                extract_body(sub_part)
        
        extract_body(payload)
        
        # Extract attachment info
        attachments = []
        def extract_attachments(part):
            if "filename" in part and part["filename"]:
                attachments.append({
                    "filename": part["filename"],
                    "mimeType": part.get("mimeType", ""),
                    "size": part.get("body", {}).get("size", 0),
                    "attachmentId": part.get("body", {}).get("attachmentId", ""),
                })
            for sub_part in part.get("parts", []):
                extract_attachments(sub_part)
        
        extract_attachments(payload)
        
        return {
            "id": msg_data["id"],
            "threadId": msg_data["threadId"],
            "labelIds": msg_data.get("labelIds", []),
            "snippet": msg_data.get("snippet", ""),
            "headers": headers,
            "from": headers.get("From", ""),
            "to": headers.get("To", ""),
            "subject": headers.get("Subject", ""),
            "date": headers.get("Date", ""),
            "body_text": body_text,
            "body_html": body_html,
            "attachments": attachments,
            "sizeEstimate": msg_data.get("sizeEstimate", 0),
        }


@registry.register(
    default_title="Get Gmail message headers",
    display_group="Gmail",
    description="Get only headers of a Gmail message (faster than full message)",
    namespace="tools.gmail",
    secrets=[gmail_oauth_secret],
)
async def get_message_headers(
    message_id: Annotated[str, Field(description="The message ID to retrieve headers for")],
    user_id: Annotated[str, Field(default="me", description="User ID or 'me' for authenticated user")] = "me",
) -> dict[str, Any]:
    """
    Get only the headers of a Gmail message.
    
    Faster than getting the full message. Useful for quick inspection
    of email metadata during investigations.
    
    Returns headers including: From, To, Subject, Date, Message-ID,
    Reply-To, Return-Path, Received, X-headers, etc.
    """
    access_token = _get_gmail_token()
    
    async with httpx.AsyncClient() as client:
        response = await client.get(
            f"https://gmail.googleapis.com/gmail/v1/users/{user_id}/messages/{message_id}",
            headers={"Authorization": f"Bearer {access_token}"},
            params={"format": "metadata"},
        )
        response.raise_for_status()
        msg_data = response.json()
        
        headers = {h["name"]: h["value"] for h in msg_data.get("payload", {}).get("headers", [])}
        
        return {
            "id": msg_data["id"],
            "threadId": msg_data["threadId"],
            "labelIds": msg_data.get("labelIds", []),
            "snippet": msg_data.get("snippet", ""),
            "headers": headers,
        }


@registry.register(
    default_title="List Gmail labels",
    display_group="Gmail",
    description="List all Gmail labels for the user",
    namespace="tools.gmail",
    secrets=[gmail_oauth_secret],
)
async def list_labels(
    user_id: Annotated[str, Field(default="me", description="User ID or 'me' for authenticated user")] = "me",
) -> list[dict[str, Any]]:
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
        return response.json().get("labels", [])


@registry.register(
    default_title="Get Gmail thread",
    display_group="Gmail",
    description="Get all messages in an email thread",
    namespace="tools.gmail",
    secrets=[gmail_oauth_secret],
)
async def get_thread(
    thread_id: Annotated[str, Field(description="The thread ID to retrieve")],
    user_id: Annotated[str, Field(default="me", description="User ID or 'me' for authenticated user")] = "me",
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
            params={"format": "full"},
        )
        response.raise_for_status()
        thread_data = response.json()
        
        messages = []
        for msg in thread_data.get("messages", []):
            headers = {h["name"]: h["value"] for h in msg.get("payload", {}).get("headers", [])}
            messages.append({
                "id": msg["id"],
                "snippet": msg.get("snippet", ""),
                "from": headers.get("From", ""),
                "to": headers.get("To", ""),
                "subject": headers.get("Subject", ""),
                "date": headers.get("Date", ""),
            })
        
        return {
            "id": thread_data["id"],
            "historyId": thread_data.get("historyId", ""),
            "messages": messages,
        }

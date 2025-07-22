"""Gmail integration for Tracecat.

This integration provides comprehensive Gmail functionality including:
- Email management (get, delete, move, search)
- Attachment handling
- User profile management
- Email sending capabilities

Authentication: Service Account or OAuth2
"""

import base64
from email.message import EmailMessage
from typing import Annotated, Any

import orjson
from google.oauth2 import service_account
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from pydantic import Field

from tracecat_registry import RegistrySecret, registry, secrets

gmail_secret = RegistrySecret(
    name="gmail",
    keys=["GMAIL_SERVICE_ACCOUNT_CREDENTIALS"],
    optional_keys=["GMAIL_DELEGATED_USER_EMAIL"],
)
"""Gmail service account credentials.

- name: `gmail`
- keys:
    - `GMAIL_SERVICE_ACCOUNT_CREDENTIALS` (JSON string of service account credentials)
- optional_keys:
    - `GMAIL_DELEGATED_USER_EMAIL` (Email of user to impersonate for domain-wide delegation)

Note: For domain-wide delegation, the service account must be configured with the necessary scopes
in the Google Admin Console.
"""


def _get_gmail_service(delegated_user=None, additional_scopes=None):
    """Create and return a Gmail service instance."""
    try:
        creds_json = secrets.get("GMAIL_SERVICE_ACCOUNT_CREDENTIALS")
        creds_dict = orjson.loads(creds_json)

        # Default scopes
        scopes = [
            "https://www.googleapis.com/auth/gmail.readonly",
            "https://www.googleapis.com/auth/gmail.send",
            "https://www.googleapis.com/auth/gmail.modify",
            "https://www.googleapis.com/auth/gmail.compose",
            "https://www.googleapis.com/auth/admin.directory.user.readonly",
        ]

        # Add additional scopes if provided
        if additional_scopes:
            scopes.extend(additional_scopes)

        # Create credentials and apply scopes
        credentials = service_account.Credentials.from_service_account_info(creds_dict)
        scoped_credentials = credentials.with_scopes(scopes)

        # Handle delegation - convert "me" to admin email
        admin_email = secrets.get("GMAIL_DELEGATED_USER_EMAIL")
        if not delegated_user or delegated_user == "me":
            delegated_user = admin_email

        if delegated_user and "@" in delegated_user:
            scoped_credentials = scoped_credentials.with_subject(delegated_user)

        return build("gmail", "v1", credentials=scoped_credentials)
    except Exception as e:
        raise ValueError(f"Failed to create Gmail service: {str(e)}")


def _get_admin_service():
    """Create and return an Admin SDK service instance for user management."""
    try:
        creds_json = secrets.get("GMAIL_SERVICE_ACCOUNT_CREDENTIALS")
        creds_dict = orjson.loads(creds_json)

        credentials = service_account.Credentials.from_service_account_info(creds_dict)
        scoped_credentials = credentials.with_scopes(
            ["https://www.googleapis.com/auth/admin.directory.user.readonly"]
        )

        # Domain-wide delegation is required for Admin SDK
        admin_email = secrets.get("GMAIL_DELEGATED_USER_EMAIL")
        if admin_email and "@" in admin_email:
            scoped_credentials = scoped_credentials.with_subject(admin_email)

        return build("admin", "directory_v1", credentials=scoped_credentials)
    except Exception as e:
        raise ValueError(f"Failed to create Admin service: {str(e)}")


@registry.register(
    default_title="Delete email",
    description="Delete a specific email message from Gmail.",
    display_group="Gmail",
    doc_url="https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/delete",
    namespace="tools.gmail",
    secrets=[gmail_secret],
)
async def delete_mail(
    message_id: Annotated[
        str,
        Field(
            ...,
            description="The ID of the message to delete",
        ),
    ],
    user_id: Annotated[
        str,
        Field(
            ...,
            description="The user's email address or 'me' for authenticated user",
        ),
    ] = "me",
) -> dict[str, Any]:
    """Delete a Gmail message."""
    try:
        service = _get_gmail_service(delegated_user=user_id)
        service.users().messages().delete(userId=user_id, id=message_id).execute()
        return {
            "message": f"Message {message_id} deleted successfully",
            "message_id": message_id,
        }
    except HttpError as error:
        raise ValueError(f"Failed to delete message: {error}")


@registry.register(
    default_title="Get email attachments",
    description="Retrieve attachments from a specific Gmail message.",
    display_group="Gmail",
    doc_url="https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages.attachments/get",
    namespace="tools.gmail",
    secrets=[gmail_secret],
)
async def get_attachments(
    message_id: Annotated[
        str,
        Field(
            ...,
            description="The ID of the message containing attachments",
        ),
    ],
    user_id: Annotated[
        str,
        Field(
            ...,
            description="The user's email address or 'me' for authenticated user",
        ),
    ] = "me",
) -> dict[str, Any]:
    """Get attachments from a Gmail message."""
    try:
        service = _get_gmail_service(delegated_user=user_id)

        # First, get the message to find attachments
        message = (
            service.users().messages().get(userId=user_id, id=message_id).execute()
        )

        attachments = []
        payload = message.get("payload", {})

        def extract_attachments(parts):
            """Recursively extract attachments from message parts."""
            attachment_list = []
            if not parts:
                return attachment_list

            for part in parts:
                if part.get("filename") and part.get("body", {}).get("attachmentId"):
                    attachment_id = part["body"]["attachmentId"]
                    attachment = (
                        service.users()
                        .messages()
                        .attachments()
                        .get(userId=user_id, messageId=message_id, id=attachment_id)
                        .execute()
                    )

                    attachment_list.append(
                        {
                            "filename": part["filename"],
                            "attachment_id": attachment_id,
                            "size": attachment.get("size", 0),
                            "data": attachment.get("data", ""),  # Base64 encoded data
                            "mime_type": part.get(
                                "mimeType", "application/octet-stream"
                            ),
                        }
                    )
                elif "parts" in part:
                    attachment_list.extend(extract_attachments(part["parts"]))
            return attachment_list

        if "parts" in payload:
            attachments = extract_attachments(payload["parts"])
        elif payload.get("filename") and payload.get("body", {}).get("attachmentId"):
            # Single attachment case
            attachment_id = payload["body"]["attachmentId"]
            attachment = (
                service.users()
                .messages()
                .attachments()
                .get(userId=user_id, messageId=message_id, id=attachment_id)
                .execute()
            )

            attachments.append(
                {
                    "filename": payload["filename"],
                    "attachment_id": attachment_id,
                    "size": attachment.get("size", 0),
                    "data": attachment.get("data", ""),
                    "mime_type": payload.get("mimeType", "application/octet-stream"),
                }
            )

        return {
            "message_id": message_id,
            "attachment_count": len(attachments),
            "attachments": attachments,
        }
    except HttpError as error:
        raise ValueError(f"Failed to get attachments: {error}")


@registry.register(
    default_title="Get email",
    description="Retrieve a specific email message from Gmail.",
    display_group="Gmail",
    doc_url="https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/get",
    namespace="tools.gmail",
    secrets=[gmail_secret],
)
async def get_mail(
    message_id: Annotated[
        str,
        Field(
            ...,
            description="The ID of the message to retrieve",
        ),
    ],
    user_id: Annotated[
        str,
        "The user's email address or 'me' for authenticated user",
        Field(
            ...,
            description="The user's email address or 'me' for authenticated user",
        ),
    ] = "me",
    format: Annotated[
        str,
        Field(
            ...,
            description="The format to return the message in (minimal, full, raw, metadata)",
        ),
    ] = "full",
) -> dict[str, Any]:
    """Get a Gmail message."""
    try:
        service = _get_gmail_service(delegated_user=user_id)
        message = (
            service.users()
            .messages()
            .get(userId=user_id, id=message_id, format=format)
            .execute()
        )

        # Extract useful information from the message
        payload = message.get("payload", {})
        headers = payload.get("headers", [])

        # Parse headers into a more usable format
        header_dict = {}
        for header in headers:
            header_dict[header["name"]] = header["value"]

        # Extract body content
        body = ""
        if payload.get("body", {}).get("data"):
            body = base64.urlsafe_b64decode(payload["body"]["data"]).decode(
                "utf-8", errors="ignore"
            )
        elif payload.get("parts"):
            for part in payload["parts"]:
                if part.get("mimeType") == "text/plain" and part.get("body", {}).get(
                    "data"
                ):
                    body = base64.urlsafe_b64decode(part["body"]["data"]).decode(
                        "utf-8", errors="ignore"
                    )
                    break

        return {
            "id": message["id"],
            "thread_id": message.get("threadId"),
            "labels": message.get("labelIds", []),
            "snippet": message.get("snippet", ""),
            "size_estimate": message.get("sizeEstimate", 0),
            "headers": header_dict,
            "subject": header_dict.get("Subject", ""),
            "from": header_dict.get("From", ""),
            "to": header_dict.get("To", ""),
            "cc": header_dict.get("Cc", ""),
            "bcc": header_dict.get("Bcc", ""),
            "date": header_dict.get("Date", ""),
            "body": body,
            "payload": payload if format == "full" else None,
        }
    except HttpError as error:
        raise ValueError(f"Failed to get message: {error}")


@registry.register(
    default_title="Get user profile",
    description="Get the Gmail profile for a specific user.",
    display_group="Gmail",
    doc_url="https://developers.google.com/workspace/gmail/api/reference/rest/v1/users/getProfile",
    namespace="tools.gmail",
    secrets=[gmail_secret],
)
async def get_user(
    user_id: Annotated[
        str,
        Field(
            ...,
            description="The user's email address or 'me' for authenticated user",
        ),
    ] = "me",
) -> dict[str, Any]:
    """Get Gmail user profile."""
    try:
        service = _get_gmail_service(delegated_user=user_id)
        profile = service.users().getProfile(userId=user_id).execute()
        return profile
    except HttpError as error:
        raise ValueError(f"Failed to get user profile: {error}")


@registry.register(
    default_title="List domain users",
    description="List users in the Google Workspace domain (requires Admin SDK access).",
    display_group="Gmail",
    doc_url="https://developers.google.com/admin-sdk/directory/reference/rest/v1/users/list",
    namespace="tools.gmail",
    secrets=[gmail_secret],
)
async def list_users(
    domain: Annotated[
        str,
        Field(
            ...,
            description="The domain to list users from",
        ),
    ] = "",
    max_results: Annotated[
        int,
        Field(
            ...,
            description="Maximum number of users to return",
        ),
    ] = 100,
    query: Annotated[
        str,
        Field(
            ...,
            description="Search query to filter users",
        ),
    ] = "",
) -> dict[str, Any]:
    """List users in the Google Workspace domain."""
    try:
        service = _get_admin_service()

        params = {"maxResults": max_results, "orderBy": "email"}

        if domain:
            params["domain"] = domain
        if query:
            params["query"] = query

        result = service.users().list(**params).execute()

        users = []
        for user in result.get("users", []):
            users.append(
                {
                    "id": user.get("id"),
                    "email": user.get("primaryEmail"),
                    "name": user.get("name", {}).get("fullName", ""),
                    "given_name": user.get("name", {}).get("givenName", ""),
                    "family_name": user.get("name", {}).get("familyName", ""),
                    "suspended": user.get("suspended", False),
                    "org_unit_path": user.get("orgUnitPath", ""),
                    "creation_time": user.get("creationTime", ""),
                    "last_login_time": user.get("lastLoginTime", ""),
                }
            )

        return {
            "users": users,
            "total_results": len(users),
            "next_page_token": result.get("nextPageToken"),
        }
    except HttpError as error:
        raise ValueError(f"Failed to list users: {error}")


@registry.register(
    default_title="Move email",
    description="Move an email by modifying its labels (add/remove labels).",
    display_group="Gmail",
    doc_url="https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/modify",
    namespace="tools.gmail",
    secrets=[gmail_secret],
)
async def move_mail(
    message_id: Annotated[
        str,
        Field(
            ...,
            description="The ID of the message to move",
        ),
    ],
    add_labels: Annotated[
        list[str],
        Field(
            ...,
            description="List of label IDs to remove from the message",
        ),
    ] = [],
    user_id: Annotated[
        str,
        Field(
            ...,
            description="The user's email address or 'me' for authenticated user",
        ),
    ] = "me",
    remove_labels: Annotated[
        list[str],
        Field(
            ...,
            description="List of label IDs to remove from the message",
        ),
    ] = [],
) -> dict[str, Any]:
    """Move a Gmail message by modifying its labels."""
    try:
        service = _get_gmail_service(delegated_user=user_id)

        body = {}
        if add_labels:
            body["addLabelIds"] = add_labels
        if remove_labels:
            body["removeLabelIds"] = remove_labels

        if not body:
            raise ValueError("Must specify either add_labels or remove_labels")

        message = (
            service.users()
            .messages()
            .modify(userId=user_id, id=message_id, body=body)
            .execute()
        )

        return {
            "message_id": message["id"],
            "labels": message.get("labelIds", []),
            "thread_id": message.get("threadId"),
        }
    except HttpError as error:
        raise ValueError(f"Failed to move message: {error}")


@registry.register(
    default_title="Move email to folder",
    description="Move an email to a specific folder/mailbox by name (Inbox, Sent, Trash, etc.).",
    display_group="Gmail",
    doc_url="https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/modify",
    namespace="tools.gmail",
    secrets=[gmail_secret],
)
async def move_mail_to_mailbox(
    message_id: Annotated[
        str,
        Field(
            ...,
            description="The ID of the message to move",
        ),
    ],
    mailbox: Annotated[
        str,
        Field(
            ...,
            description="The mailbox/folder to move to (INBOX, SENT, TRASH, SPAM, DRAFT, IMPORTANT, STARRED, UNREAD)",
        ),
    ],
    user_id: Annotated[
        str,
        Field(
            ...,
            description="The user's email address or 'me' for authenticated user",
        ),
    ] = "me",
) -> dict[str, Any]:
    """Move a Gmail message to a specific mailbox/folder."""
    try:
        service = _get_gmail_service(delegated_user=user_id)

        # Convert common mailbox names to Gmail labels
        mailbox_mapping = {
            "INBOX": "INBOX",
            "SENT": "SENT",
            "TRASH": "TRASH",
            "SPAM": "SPAM",
            "DRAFT": "DRAFT",
            "IMPORTANT": "IMPORTANT",
            "STARRED": "STARRED",
            "UNREAD": "UNREAD",
        }

        target_label = mailbox_mapping.get(mailbox.upper(), mailbox)

        # Get current message to see its labels
        current_message = (
            service.users()
            .messages()
            .get(userId=user_id, id=message_id, format="minimal")
            .execute()
        )

        current_labels = current_message.get("labelIds", [])

        # Determine what labels to add/remove
        add_labels = [target_label] if target_label not in current_labels else []
        remove_labels = []

        # Remove conflicting labels when moving to certain mailboxes
        if target_label == "TRASH":
            remove_labels = [
                label for label in current_labels if label in ["INBOX", "SENT"]
            ]
        elif target_label == "INBOX":
            remove_labels = [
                label for label in current_labels if label in ["TRASH", "SPAM"]
            ]

        body = {}
        if add_labels:
            body["addLabelIds"] = add_labels
        if remove_labels:
            body["removeLabelIds"] = remove_labels

        if not body:
            return {
                "message_id": message_id,
                "message": f"Message already in {mailbox}",
                "labels": current_labels,
            }

        message = (
            service.users()
            .messages()
            .modify(userId=user_id, id=message_id, body=body)
            .execute()
        )

        return {
            "message_id": message["id"],
            "moved_to": mailbox,
            "labels": message.get("labelIds", []),
            "thread_id": message.get("threadId"),
        }
    except HttpError as error:
        raise ValueError(f"Failed to move message to {mailbox}: {error}")


@registry.register(
    default_title="Search emails",
    description="Search for emails in a specific user's mailbox using Gmail search syntax.",
    display_group="Gmail",
    doc_url="https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list",
    namespace="tools.gmail",
    secrets=[gmail_secret],
)
async def search(
    query: Annotated[
        str,
        Field(
            ...,
            description="Gmail search query (e.g., 'from:example@gmail.com subject:urgent is:unread')",
        ),
    ],
    user_id: Annotated[
        str,
        Field(
            ...,
            description="The user's email address or 'me' for authenticated user",
        ),
    ] = "me",
    max_results: Annotated[
        int,
        Field(
            ...,
            description="Maximum number of messages to return. Defaults is 100.",
        ),
    ] = 100,
    include_spam_trash: Annotated[
        bool,
        Field(
            ...,
            description="Include messages from SPAM and TRASH. Default is False.",
        ),
    ] = False,
    next_page_token: Annotated[
        str,
        Field(
            ...,
            description="Next page token",
        ),
    ] = "",
    format: Annotated[
        str,
        Field(
            ...,
            description="The format to return the message in (minimal, full, raw, metadata)",
        ),
    ] = "full",
) -> dict[str, Any]:
    """Search for emails in Gmail."""
    try:
        service = _get_gmail_service(delegated_user=user_id)

        params = {
            "userId": user_id,
            "q": query,
            "maxResults": max_results,
            "includeSpamTrash": include_spam_trash,
            "pageToken": next_page_token,
        }

        result = service.users().messages().list(**params).execute()
        messages = result.get("messages", [])

        # Get detailed information for each message
        detailed_messages = []
        for msg in messages:
            try:
                detailed_msg = (
                    service.users()
                    .messages()
                    .get(
                        userId=user_id,
                        id=msg["id"],
                        format=format,
                        metadataHeaders=["From", "To", "Subject", "Date"],
                    )
                    .execute()
                )

                headers = {}
                for header in detailed_msg.get("payload", {}).get("headers", []):
                    headers[header["name"]] = header["value"]

                body = base64.urlsafe_b64decode(
                    detailed_msg.get("payload", {}).get("body", {}).get("data", "")
                ).decode("utf-8")

                parts = detailed_msg.get("payload", {}).get("parts", [])
                for part in parts:
                    if part.get("mimeType") == "text/html":
                        body = base64.urlsafe_b64decode(
                            part.get("body", {}).get("data", "")
                        ).decode("utf-8")
                    elif part.get("mimeType") == "text/plain":
                        body = base64.urlsafe_b64decode(
                            part.get("body", {}).get("data", "")
                        ).decode("utf-8")

                detailed_messages.append(
                    {
                        "id": detailed_msg["id"],
                        "thread_id": detailed_msg.get("threadId"),
                        "labels": detailed_msg.get("labelIds", []),
                        "snippet": detailed_msg.get("snippet", ""),
                        "body": body,
                        "from": headers.get("From", ""),
                        "to": headers.get("To", ""),
                        "subject": headers.get("Subject", ""),
                        "date": headers.get("Date", ""),
                    }
                )
            except HttpError:
                # If we can't get details for a message, skip it
                continue

        return {
            "query": query,
            "result_size_estimate": result.get("resultSizeEstimate", 0),
            "messages": detailed_messages,
            "next_page_token": result.get("nextPageToken"),
        }
    except HttpError as error:
        raise ValueError(f"Failed to search messages: {error}")


@registry.register(
    default_title="Search all user mailboxes",
    description="Search emails across multiple user mailboxes (requires domain admin access).",
    display_group="Gmail",
    doc_url="https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/list",
    namespace="tools.gmail",
    secrets=[gmail_secret],
)
async def search_all_mailboxes(
    query: Annotated[
        str,
        Field(
            ...,
            description="Gmail search query to apply to all mailboxes",
        ),
    ],
    domain: Annotated[
        str,
        Field(
            ...,
            description="Domain to search across (optional)",
        ),
    ] = "",
    max_users: Annotated[
        int,
        Field(
            ...,
            description="Maximum number of users to search",
        ),
    ] = 10,
    max_results_per_user: Annotated[
        int,
        Field(
            ...,
            description="Maximum messages per user",
        ),
    ] = 10,
) -> dict[str, Any]:
    """Search emails across multiple user mailboxes."""
    try:
        # First, get list of users
        admin_service = _get_admin_service()
        gmail_service = _get_gmail_service()

        params = {"maxResults": max_users, "orderBy": "email"}
        if domain:
            params["domain"] = domain

        users_result = admin_service.users().list(**params).execute()
        users = users_result.get("users", [])

        all_results = []
        for user in users:
            user_email = user.get("primaryEmail")
            if not user_email:
                continue

            try:
                # Search this user's mailbox
                search_params = {
                    "userId": user_email,
                    "q": query,
                    "maxResults": max_results_per_user,
                    "includeSpamTrash": False,
                }

                result = (
                    gmail_service.users().messages().list(**search_params).execute()
                )
                messages = result.get("messages", [])

                user_results = []
                for msg in messages[:max_results_per_user]:
                    try:
                        detailed_msg = (
                            gmail_service.users()
                            .messages()
                            .get(
                                userId=user_email,
                                id=msg["id"],
                                format="metadata",
                                metadataHeaders=["From", "To", "Subject", "Date"],
                            )
                            .execute()
                        )

                        headers = {}
                        for header in detailed_msg.get("payload", {}).get(
                            "headers", []
                        ):
                            headers[header["name"]] = header["value"]

                        user_results.append(
                            {
                                "id": detailed_msg["id"],
                                "thread_id": detailed_msg.get("threadId"),
                                "snippet": detailed_msg.get("snippet", ""),
                                "from": headers.get("From", ""),
                                "to": headers.get("To", ""),
                                "subject": headers.get("Subject", ""),
                                "date": headers.get("Date", ""),
                            }
                        )
                    except HttpError:
                        continue

                if user_results:
                    all_results.append(
                        {
                            "user_email": user_email,
                            "user_name": user.get("name", {}).get("fullName", ""),
                            "message_count": len(user_results),
                            "messages": user_results,
                        }
                    )
            except HttpError:
                # Skip users we can't access
                continue

        return {
            "query": query,
            "users_searched": len(users),
            "users_with_results": len(all_results),
            "total_messages": sum(user["message_count"] for user in all_results),
            "results": all_results,
        }
    except HttpError as error:
        raise ValueError(f"Failed to search all mailboxes: {error}")


@registry.register(
    default_title="Send email",
    description="Send an email message through Gmail.",
    display_group="Gmail",
    doc_url="https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send",
    namespace="tools.gmail",
    secrets=[gmail_secret],
)
async def send_mail(
    to: Annotated[
        str | list[str],
        Field(
            ...,
            description="Recipient email address(es)",
        ),
    ],
    subject: Annotated[
        str,
        "Email subject",
        Field(
            ...,
            description="Email subject",
        ),
    ],
    body: Annotated[
        str,
        Field(
            ...,
            description="Email body content",
        ),
    ],
    from_email: Annotated[
        str,
        Field(
            ...,
            description="Sender email address (must be authorized)",
        ),
    ] = "",
    cc: Annotated[
        str | list[str],
        Field(
            ...,
            description="CC recipient email address(es)",
        ),
    ] = [],
    bcc: Annotated[
        str | list[str],
        Field(
            ...,
            description="BCC recipient email address(es)",
        ),
    ] = [],
    content_type: Annotated[
        str,
        Field(
            ...,
            description="Content type (text/plain or text/html)",
        ),
    ] = "text/plain",
    user_id: Annotated[
        str,
        Field(
            ...,
            description="The user's email address or 'me' for authenticated user",
        ),
    ] = "me",
) -> dict[str, Any]:
    """Send an email through Gmail."""
    try:
        service = _get_gmail_service(delegated_user=user_id)

        # Create the email message
        message = EmailMessage()

        # Set content
        if content_type == "text/html":
            message.set_content(body, subtype="html")
        else:
            message.set_content(body)

        # Handle recipients
        if isinstance(to, str):
            to = [to]
        if isinstance(cc, str):
            cc = [cc] if cc else []
        if isinstance(bcc, str):
            bcc = [bcc] if bcc else []

        # Set headers
        message["To"] = ", ".join(to)
        if cc:
            message["Cc"] = ", ".join(cc)
        if bcc:
            message["Bcc"] = ", ".join(bcc)
        if from_email:
            message["From"] = from_email
        message["Subject"] = subject

        # Encode the message
        encoded_message = base64.urlsafe_b64encode(message.as_bytes()).decode()

        # Send the message
        send_message = (
            service.users()
            .messages()
            .send(userId=user_id, body={"raw": encoded_message})
            .execute()
        )

        return {
            "message_id": send_message["id"],
            "thread_id": send_message.get("threadId"),
            "to": to,
            "cc": cc,
            "bcc": bcc,
            "subject": subject,
            "status": "sent",
        }
    except HttpError as error:
        raise ValueError(f"Failed to send message: {error}")
